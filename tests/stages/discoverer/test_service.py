"""DiscovererService: 4-URL crawl, fail-soft, new fields propagation."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from edx.config import TickerEntry
from edx.http.client import EDisclosureClient
from edx.stages.discoverer.service import (
    REPORT_TYPE_CODES,
    DiscovererService,
)
from edx.storage import Database, PublicationsRepo, TickersRepo

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "edisclosure_real"


def _per_type_transport(
    pages: dict[int, tuple[int, str]],
) -> tuple[httpx.MockTransport, list[tuple[str, str]]]:
    """Mock transport keyed by ``type=`` query param.

    ``pages[type_code] = (status, html)``. Missing types return 404.
    Tracks every request (path + raw query) so tests can assert iteration.
    """
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        seen.append((request.url.path, request.url.query.decode("ascii")))
        if request.url.path != "/portal/files.aspx":
            return httpx.Response(404, text="not found")
        type_str = request.url.params.get("type", "")
        try:
            type_code = int(type_str)
        except ValueError:
            return httpx.Response(404, text="bad type")
        if type_code not in pages:
            return httpx.Response(404, text="missing type")
        status, html = pages[type_code]
        return httpx.Response(
            status, text=html, headers={"Content-Type": "text/html"}
        )

    return httpx.MockTransport(handler), seen


def _make_db_with_ticker(
    tmp_path: Path, ticker: str, e_disclosure_id: str = "3043"
) -> tuple[Database, sqlite3.Connection]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    conn = db.connect()
    TickersRepo(db, conn).upsert_from_config(
        [TickerEntry(ticker=ticker, e_disclosure_id=e_disclosure_id, name=ticker)]
    )
    return db, conn


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- happy path: SBER all four types --------------------------------------


@pytest.mark.asyncio
async def test_run_iterates_four_types_and_writes_publications(
    tmp_path: Path,
) -> None:
    """Discoverer must hit ``files.aspx`` for type 2/3/4/5 and skip type=1."""
    pages = {
        2: (200, _load("sber_type_2.html")),
        3: (200, _load("sber_type_3.html")),
        4: (200, _load("sber_type_4.html")),
        # type=5 (issuer report) — issuer doesn't publish it: 404 ok.
        5: (404, ""),
    }
    transport, seen = _per_type_transport(pages)
    db, conn = _make_db_with_ticker(tmp_path, "SBER", e_disclosure_id="3043")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=transport,
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=20
            )
            new_pubs = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="3043", name="Sberbank")],
                since={"SBER": None},
            )
        # 4 (type=2) + 13 (type=3) + 7 (type=4) = 24, type=5 was 404.
        assert len(new_pubs) == 24
        # Service hit exactly four files.aspx URLs (type=2,3,4,5),
        # never type=1. Order may vary but the set must match.
        crawled_types = sorted(
            int(query.split("type=")[1])
            for path, query in seen
            if path == "/portal/files.aspx"
        )
        assert crawled_types == sorted(REPORT_TYPE_CODES)
        assert 1 not in crawled_types
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_persists_patch17_fields_to_repo(tmp_path: Path) -> None:
    """The new report_type_code / period columns must round-trip into SQLite."""
    pages = {
        2: (200, "<html><body></body></html>"),
        3: (200, "<html><body></body></html>"),
        4: (200, _load("sber_type_4.html")),
        5: (404, ""),
    }
    transport, _ = _per_type_transport(pages)
    db, conn = _make_db_with_ticker(tmp_path, "SBER", e_disclosure_id="3043")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=transport,
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=10
            )
            await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="3043", name="Sberbank")],
                since={"SBER": None},
            )

        rows = list(
            conn.execute(
                "SELECT publication_id, report_type_code, "
                "reporting_period_year, reporting_period_type "
                "FROM publications ORDER BY publication_date"
            )
        )
        assert len(rows) == 7
        assert all(row["report_type_code"] == 4 for row in rows)
        assert all(row["reporting_period_year"] is not None for row in rows)
        period_types = {row["reporting_period_type"] for row in rows}
        assert {"Q1", "FY"}.issubset(period_types)
    finally:
        conn.close()


# --- fail-soft: missing types --------------------------------------------


@pytest.mark.asyncio
async def test_run_continues_when_a_type_returns_404(tmp_path: Path) -> None:
    """LKOH-style case: id=17 has no type=4 — service must still collect type=3."""
    pages = {
        2: (404, ""),
        3: (200, _load("lkoh_type_3.html")),
        4: (404, ""),  # the LKOH-style hole
        5: (404, ""),
    }
    transport, _ = _per_type_transport(pages)
    db, conn = _make_db_with_ticker(tmp_path, "LKOH", e_disclosure_id="17")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=transport,
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=20
            )
            new_pubs = await service.run(
                [TickerEntry(ticker="LKOH", e_disclosure_id="17", name="Lukoil")],
                since={"LKOH": None},
            )
        assert len(new_pubs) == 60
        assert all(pub.report_type_code == 3 for pub in new_pubs)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_continues_when_a_type_returns_empty_table(
    tmp_path: Path,
) -> None:
    """200 OK with no ``table.files-table`` is the same as a missing type."""
    empty_page = "<html><body><div>No items.</div></body></html>"
    pages = {
        2: (200, empty_page),
        3: (200, _load("sber_type_3.html")),
        4: (200, empty_page),
        5: (200, empty_page),
    }
    transport, _ = _per_type_transport(pages)
    db, conn = _make_db_with_ticker(tmp_path, "SBER", e_disclosure_id="3043")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=transport,
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=10
            )
            new_pubs = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="3043", name="Sberbank")],
                since={"SBER": None},
            )
        assert len(new_pubs) == 13  # only type=3 returned data
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_continues_when_one_type_returns_500(tmp_path: Path) -> None:
    """5xx on one type must not abort the whole crawl for the ticker."""
    pages = {
        2: (500, "boom"),
        3: (200, _load("sber_type_3.html")),
        4: (200, _load("sber_type_4.html")),
        5: (404, ""),
    }
    transport, _ = _per_type_transport(pages)
    db, conn = _make_db_with_ticker(tmp_path, "SBER", e_disclosure_id="3043")
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
                client, publications_repo, backfill_years=10
            )
            new_pubs = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="3043", name="Sberbank")],
                since={"SBER": None},
            )
        # Same shape as if type=2 had been empty: 13+7=20 rows.
        assert len(new_pubs) == 20
    finally:
        conn.close()


# --- incremental + helpers (unchanged contracts) --------------------------


@pytest.mark.asyncio
async def test_run_filters_by_strict_greater_than(tmp_path: Path) -> None:
    pages = {
        2: (404, ""),
        3: (404, ""),
        4: (200, _load("sber_type_4.html")),
        5: (404, ""),
    }
    transport, _ = _per_type_transport(pages)
    db, conn = _make_db_with_ticker(tmp_path, "SBER", e_disclosure_id="3043")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=transport,
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=10
            )
            # First run without filter: expect 7 type=4 publications.
            first = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="3043", name="Sberbank")],
                since={"SBER": None},
            )
            assert len(first) == 7
            latest = publications_repo.latest_publication_date("SBER")
            assert latest is not None
            # Second run with since=latest: nothing new.
            second = await service.run(
                [TickerEntry(ticker="SBER", e_disclosure_id="3043", name="Sberbank")],
                since={"SBER": latest},
            )
            assert second == []
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


@pytest.mark.asyncio
async def test_run_bootstraps_unseen_ticker_with_old_publications(
    tmp_path: Path,
) -> None:
    """Ticker with no prior DB entries (since=None) must import all-time history.

    With backfill_years=1 the cutoff would be ~2025-05-03, which would exclude
    publications dated 2015.  Since the ticker has never been seen (since=None),
    _BOOTSTRAP_CUTOFF ("1900-01-01") is used instead so the full archive is
    imported regardless of how old the publications are.
    """
    old_html = (
        '<table class="zebra noBorderTbl centerHeader files-table"><tbody>'
        "<tr><th>#</th><th>Тип</th><th>Период</th>"
        "<th>Основание</th><th>Дата</th><th>Файл</th><th></th></tr>"
        + "".join(
            f"<tr>"
            f"<td>{i}</td>"
            f"<td>РСБУ</td>"
            f"<td>2015, 3 месяца</td>"
            f'<td class="date-cell">01.01.2015</td>'
            f'<td class="date-cell">15.03.2015</td>'
            f'<td class="file-cell">'
            f'<a class="file-link" href="/portal/FileLoad.ashx?Fileid={1000 + i}"'
            f' data-fileid="{1000 + i}">zip</a></td>'
            f"<td></td></tr>"
            for i in range(1, 4)
        )
        + "</tbody></table>"
    )
    pages = {2: (404, ""), 3: (200, old_html), 4: (404, ""), 5: (404, "")}
    transport, _ = _per_type_transport(pages)
    db, conn = _make_db_with_ticker(tmp_path, "NOVA", e_disclosure_id="99")
    try:
        publications_repo = PublicationsRepo(db, conn)
        async with EDisclosureClient(
            base_url="https://example.test",
            user_agent="edx-test/1.0",
            requests_per_second=100.0,
            respect_robots=False,
            transport=transport,
        ) as client:
            service = DiscovererService(
                client, publications_repo, backfill_years=1
            )
            new_pubs = await service.run(
                [TickerEntry(ticker="NOVA", e_disclosure_id="99", name="Nova")],
                since={"NOVA": None},
            )
        assert len(new_pubs) == 3  # all 2015 pubs bootstrapped despite backfill_years=1
        assert all(pub.publication_date == "2015-03-15" for pub in new_pubs)
    finally:
        conn.close()
