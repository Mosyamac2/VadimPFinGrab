"""Discoverer stage: walk listing pages, write rows to ``publications``.

Patch 16 changes the Discoverer to hit four URLs per ticker — one per report
type — instead of the synthetic ``/portal/company.aspx?id=X`` issuer card:

- ``/portal/files.aspx?id=X&type=2`` — Annual report (metadata-only source)
- ``/portal/files.aspx?id=X&type=3`` — RSBU
- ``/portal/files.aspx?id=X&type=4`` — IFRS
- ``/portal/files.aspx?id=X&type=5`` — Issuer report

``type=1`` (statutory documents) is intentionally not crawled — these are not
periodic reports and have no place in the metric pipeline.

Events (``publication_type='event'``) come from a different feed; until that
feed is implemented, event publications are seeded into the table by other
means (operator import, integration tests).

Fail-soft contract: when an issuer doesn't publish a particular type
(real example: LKOH ``id=17`` has no ``type=4``), the page returns 200 OK
with no ``table.files-table`` — service logs ``info`` and moves on. Same for
404/410 and exhausted retries on 5xx — never aborts the whole run for one
(ticker, type) combination.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Final

import httpx

from edx.config import TickerEntry
from edx.http.client import EDisclosureClient
from edx.http.exceptions import RobotsDisallowedError
from edx.logging_setup import get_logger
from edx.stages.discoverer.parser import (
    DiscoveredPublication,
    parse_listing_page,
)
from edx.storage import PublicationsRepo

# Type codes the Discoverer crawls per ticker. Order is informative only —
# upserts are idempotent and the rest of the pipeline doesn't depend on the
# scrape order. ``type=1`` (statutory docs) is excluded by design.
REPORT_TYPE_CODES: Final[tuple[int, ...]] = (2, 3, 4, 5)


class DiscovererService:
    """Glue between :class:`EDisclosureClient` and ``publications`` table."""

    def __init__(
        self,
        client: EDisclosureClient,
        publications_repo: PublicationsRepo,
        *,
        backfill_years: int,
    ) -> None:
        if backfill_years < 1:
            raise ValueError("backfill_years must be >= 1")
        self.client = client
        self.publications_repo = publications_repo
        self.backfill_years = backfill_years
        self._log = get_logger("edx.stages.discoverer")

    async def run(
        self,
        tickers: Iterable[TickerEntry],
        since: Mapping[str, str | None] | None = None,
    ) -> list[DiscoveredPublication]:
        """For every ticker hit four listing URLs, parse, filter, persist.

        ``since[ticker]`` is the last known publication date (ISO ``YYYY-MM-DD``)
        for that ticker, or ``None``/missing to fall back to the configured
        backfill horizon (today − ``backfill_years``).
        """
        since_map = dict(since or {})
        backfill_cutoff = self._backfill_cutoff()
        new_publications: list[DiscoveredPublication] = []

        for ticker in tickers:
            cutoff = since_map.get(ticker.ticker) or backfill_cutoff
            for type_code in REPORT_TYPE_CODES:
                pubs_for_type = await self._discover_one_type(
                    ticker, type_code=type_code, cutoff=cutoff
                )
                new_publications.extend(pubs_for_type)

        return new_publications

    async def _discover_one_type(
        self,
        ticker: TickerEntry,
        *,
        type_code: int,
        cutoff: str,
    ) -> list[DiscoveredPublication]:
        url = self._files_listing_path(ticker.e_disclosure_id, type_code)

        try:
            response = await self.client.get(url)
        except RobotsDisallowedError as exc:
            self._log.error(
                "discoverer_robots_disallowed",
                ticker=ticker.ticker,
                type_code=type_code,
                url=exc.url,
            )
            return []
        except httpx.HTTPError as exc:
            self._log.warning(
                "discoverer_fetch_failed",
                ticker=ticker.ticker,
                type_code=type_code,
                url=url,
                error=str(exc),
            )
            return []

        if response.status_code in (404, 410):
            self._log.info(
                "discoverer_no_publications_for_type",
                ticker=ticker.ticker,
                type_code=type_code,
                status=response.status_code,
            )
            return []
        if response.status_code != 200:
            self._log.warning(
                "discoverer_non_200",
                ticker=ticker.ticker,
                type_code=type_code,
                url=url,
                status=response.status_code,
            )
            return []

        parsed = parse_listing_page(
            response.text,
            base_url=self.client.base_url,
            ticker=ticker.ticker,
            type_code=type_code,
        )
        for warning in parsed.warnings:
            self._log.warning(
                "discoverer_parse_warning",
                ticker=ticker.ticker,
                type_code=type_code,
                detail=warning,
            )
        if not parsed.publications:
            self._log.info(
                "discoverer_no_publications_for_type",
                ticker=ticker.ticker,
                type_code=type_code,
                status=200,
            )
            return []

        new_for_ticker = [
            pub
            for pub in parsed.publications
            if pub.publication_date > cutoff
        ]
        inserted_pubs: list[DiscoveredPublication] = []
        for pub in new_for_ticker:
            was_new = self.publications_repo.upsert_discovered(
                publication_id=pub.publication_id,
                ticker=ticker.ticker,
                publication_type=pub.publication_type,
                publication_date=pub.publication_date,
                source_url=pub.source_url,
                report_type_code=pub.report_type_code,
                report_type_label=pub.report_type_label,
                reporting_period_year=pub.reporting_period_year,
                reporting_period_type=pub.reporting_period_type,
            )
            if was_new:
                inserted_pubs.append(pub)

        self._log.info(
            "ticker_type_discovered",
            ticker=ticker.ticker,
            type_code=type_code,
            cutoff_date=cutoff,
            found=len(parsed.publications),
            new=len(new_for_ticker),
            inserted=len(inserted_pubs),
        )
        return inserted_pubs

    def _files_listing_path(
        self, e_disclosure_id: str, type_code: int
    ) -> str:
        return f"/portal/files.aspx?id={e_disclosure_id}&type={type_code}"

    def _backfill_cutoff(self) -> str:
        today = datetime.now(UTC).date()
        return (today - timedelta(days=365 * self.backfill_years)).isoformat()


def compute_since(
    publications_repo: PublicationsRepo,
    tickers: Iterable[TickerEntry],
) -> dict[str, str | None]:
    """Build the ``since`` map by querying the latest publication per ticker."""
    return {
        t.ticker: publications_repo.latest_publication_date(t.ticker)
        for t in tickers
    }


def backfill_cutoff_from(reference: date | None, *, years: int) -> str:
    """Helper used by the CLI/orchestrator to compute the backfill horizon."""
    base = reference or datetime.now(UTC).date()
    return (base - timedelta(days=365 * years)).isoformat()
