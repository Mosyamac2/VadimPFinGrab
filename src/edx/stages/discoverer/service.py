"""Discoverer stage entry point: walk issuer cards, write rows to ``publications``."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta

import httpx

from edx.config import TickerEntry
from edx.http.client import EDisclosureClient
from edx.http.exceptions import RobotsDisallowedError
from edx.logging_setup import get_logger
from edx.stages.discoverer.parser import (
    DiscoveredPublication,
    parse_issuer_card,
)
from edx.storage import PublicationsRepo


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
        """For every ticker fetch its card, parse, filter by date, persist new rows.

        ``since[ticker]`` is the last known publication date (ISO ``YYYY-MM-DD``)
        for that ticker, or ``None``/missing to fall back to the configured
        backfill horizon (today − ``backfill_years``).
        """
        since_map = dict(since or {})
        backfill_cutoff = self._backfill_cutoff()
        new_publications: list[DiscoveredPublication] = []

        for ticker in tickers:
            cutoff = since_map.get(ticker.ticker) or backfill_cutoff
            url = self._issuer_card_path(ticker.e_disclosure_id)
            try:
                response = await self.client.get(url)
            except RobotsDisallowedError as exc:
                self._log.error(
                    "discoverer_robots_disallowed",
                    ticker=ticker.ticker,
                    url=exc.url,
                )
                continue
            except httpx.HTTPError as exc:
                self._log.error(
                    "discoverer_fetch_failed",
                    ticker=ticker.ticker,
                    url=url,
                    error=str(exc),
                )
                continue

            if response.status_code != 200:
                self._log.warning(
                    "discoverer_non_200",
                    ticker=ticker.ticker,
                    url=url,
                    status=response.status_code,
                )
                continue

            parsed = parse_issuer_card(
                response.text,
                base_url=self.client.base_url,
                ticker=ticker.ticker,
            )
            for warning in parsed.warnings:
                self._log.warning(
                    "discoverer_parse_warning",
                    ticker=ticker.ticker,
                    detail=warning,
                )

            new_for_ticker = [
                pub
                for pub in parsed.publications
                if pub.publication_date > cutoff
            ]
            inserted = 0
            for pub in new_for_ticker:
                was_new = self.publications_repo.upsert_discovered(
                    publication_id=pub.publication_id,
                    ticker=ticker.ticker,
                    publication_type=pub.publication_type,
                    publication_date=pub.publication_date,
                    source_url=pub.source_url,
                )
                if was_new:
                    inserted += 1
                    new_publications.append(pub)

            self._log.info(
                "ticker_discovered",
                ticker=ticker.ticker,
                cutoff_date=cutoff,
                found=len(parsed.publications),
                new=len(new_for_ticker),
                inserted=inserted,
            )

        return new_publications

    def _issuer_card_path(self, e_disclosure_id: str) -> str:
        return f"/portal/company.aspx?id={e_disclosure_id}"

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
