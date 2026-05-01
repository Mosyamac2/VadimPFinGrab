"""DiscovererService: incremental filtering, repo writes."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from edx.config import TickerEntry
from edx.http.client import EDisclosureClient
from edx.stages.discoverer.service import DiscovererService
from edx.storage import Database, PublicationsRepo, TickersRepo

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "edisclosure"


def _full_card_transport(html: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(200, text=html)

    return httpx.MockTransport(handler)


def _make_db_with_ticker(
    tmp_path: Path, ticker: str
) -> tuple[Database, sqlite3.Connection]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    conn = db.connect()
    TickersRepo(db, conn).upsert_from_config(
        [TickerEntry(ticker=ticker, e_disclosure_id="42", name=ticker)]
    )
    return db, conn


@pytest.mark.asyncio
async def test_run_writes_publications_for_ticker(tmp_path: Path) -> None:
    html = (FIXTURES / "issuer_full.html").read_text(encoding="utf-8")
    db, conn = _make_db_with_ticker(tmp_path, "SBER")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=_full_card_transport(html),
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=3
            )
            new_pubs = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="42", name="Sberbank")],
                since={"SBER": None},
            )
        assert len(new_pubs) == 5
        in_db = list(
            conn.execute(
                "SELECT publication_id, ticker, publication_type, "
                "publication_date, status FROM publications ORDER BY "
                "publication_date"
            )
        )
        assert len(in_db) == 5
        assert all(row[1] == "SBER" for row in in_db)
        assert all(row[4] == "discovered" for row in in_db)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_is_incremental(tmp_path: Path) -> None:
    """Publications already in the table at date D must not be re-added."""
    html = (FIXTURES / "issuer_full.html").read_text(encoding="utf-8")
    db, conn = _make_db_with_ticker(tmp_path, "SBER")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=_full_card_transport(html),
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=3
            )
            # First run — backfill all 5 publications.
            first = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="42", name="Sberbank")],
                since={"SBER": None},
            )
            assert len(first) == 5

            # Second run with since=latest — nothing new should be returned,
            # nothing new written.
            latest = publications_repo.latest_publication_date("SBER")
            assert latest is not None
            second = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="42", name="Sberbank")],
                since={"SBER": latest},
            )
            assert second == []

            count = conn.execute(
                "SELECT COUNT(*) AS c FROM publications"
            ).fetchone()["c"]
            assert count == 5
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_filters_by_strict_greater_than(tmp_path: Path) -> None:
    html = (FIXTURES / "issuer_full.html").read_text(encoding="utf-8")
    db, conn = _make_db_with_ticker(tmp_path, "SBER")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=_full_card_transport(html),
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=3
            )
            # since = 2026-03-15: only 2026-04-05 strictly > since.
            new_pubs = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="42", name="Sberbank")],
                since={"SBER": "2026-03-15"},
            )
        assert len(new_pubs) == 1
        assert new_pubs[0].publication_date == "2026-04-05"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_skips_ticker_when_card_returns_500(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(500, text="server error")

    transport = httpx.MockTransport(handler)
    db, conn = _make_db_with_ticker(tmp_path, "ROSN")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            max_retries=0,
            respect_robots=False,
            transport=transport,
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=3
            )
            new_pubs = await service.run(
                [TickerEntry(ticker="ROSN", e_disclosure_id="42", name="Rosneft")],
                since={"ROSN": None},
            )
        assert new_pubs == []
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM publications"
        ).fetchone()["c"]
        assert count == 0
    finally:
        conn.close()


def test_backfill_cutoff_helper() -> None:
    from datetime import date

    from edx.stages.discoverer.service import backfill_cutoff_from

    cutoff = backfill_cutoff_from(date(2026, 5, 1), years=3)
    assert cutoff <= "2023-05-02"
    assert cutoff >= "2023-04-30"


def test_compute_since_returns_none_for_unknown_ticker(tmp_path: Path) -> None:
    from edx.stages.discoverer.service import compute_since

    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        result = compute_since(
            publications_repo,
            [TickerEntry(ticker="XYZ", e_disclosure_id="1", name="X")],
        )
    assert result == {"XYZ": None}
