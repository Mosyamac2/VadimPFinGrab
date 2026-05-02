"""PublicationsRepo: lifecycle, CHECK constraints, latest date."""

from __future__ import annotations

import sqlite3

import pytest

from edx.config import TickerEntry
from edx.storage import Database, PublicationsRepo, TickersRepo


@pytest.fixture
def seeded(tmp_db: Database, conn: sqlite3.Connection) -> tuple[Database, sqlite3.Connection]:
    TickersRepo(tmp_db, conn).upsert_from_config(
        [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
    )
    return tmp_db, conn


def test_upsert_discovered_inserts_new(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    inserted = repo.upsert_discovered(
        publication_id="p-001",
        ticker="SBER",
        publication_type="report",
        publication_date="2026-03-01",
        source_url="https://example.com/p1",
    )
    assert inserted is True
    pub = repo.get_by_id("p-001")
    assert pub is not None
    assert pub.status == "discovered"


def test_upsert_discovered_idempotent(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    repo.upsert_discovered(
        publication_id="p-1", ticker="SBER", publication_type="event",
        publication_date="2026-03-01", source_url="https://x",
    )
    second = repo.upsert_discovered(
        publication_id="p-1", ticker="SBER", publication_type="event",
        publication_date="2026-03-01", source_url="https://x",
    )
    assert second is False


def test_status_full_lifecycle(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    repo.upsert_discovered(
        publication_id="p-1", ticker="SBER", publication_type="report",
        publication_date="2026-03-01", source_url="https://x",
    )
    for status in (
        "downloaded",
        "unpacked",
        "classified",
        "extracted",
        "validated",
        "written",
    ):
        repo.mark_status("p-1", status)  # type: ignore[arg-type]
        pub = repo.get_by_id("p-1")
        assert pub is not None and pub.status == status


def test_mark_status_unknown_raises(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    repo.upsert_discovered(
        publication_id="p-1", ticker="SBER", publication_type="report",
        publication_date="2026-03-01", source_url="https://x",
    )
    with pytest.raises(ValueError):
        repo.mark_status("p-1", "mystery")  # type: ignore[arg-type]


def test_mark_status_records_error_and_hash(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    repo.upsert_discovered(
        publication_id="p-1", ticker="SBER", publication_type="report",
        publication_date="2026-03-01", source_url="https://x",
    )
    repo.mark_status("p-1", "downloaded", file_hash="deadbeef" * 8)
    pub = repo.get_by_id("p-1")
    assert pub is not None
    assert pub.file_hash is not None and pub.file_hash.startswith("deadbeef")
    repo.mark_status("p-1", "failed", error="connection reset")
    pub2 = repo.get_by_id("p-1")
    assert pub2 is not None and pub2.last_error == "connection reset"


def test_latest_publication_date_returns_max(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    repo.upsert_discovered(
        publication_id="p-1", ticker="SBER", publication_type="report",
        publication_date="2025-06-15", source_url="https://x1",
    )
    repo.upsert_discovered(
        publication_id="p-2", ticker="SBER", publication_type="event",
        publication_date="2026-04-01", source_url="https://x2",
    )
    assert repo.latest_publication_date("SBER") == "2026-04-01"
    assert repo.latest_publication_date("UNKNOWN") is None


def test_list_by_status(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    repo.upsert_discovered(
        publication_id="p-1", ticker="SBER", publication_type="report",
        publication_date="2026-01-01", source_url="https://x1",
    )
    repo.upsert_discovered(
        publication_id="p-2", ticker="SBER", publication_type="event",
        publication_date="2026-02-01", source_url="https://x2",
    )
    repo.mark_status("p-2", "downloaded")
    discovered = repo.list_by_status("discovered")
    assert [p.publication_id for p in discovered] == ["p-1"]


def test_upsert_discovered_writes_period_columns(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    """Patch 17: report type + period fields round-trip through INSERT and SELECT."""
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    inserted = repo.upsert_discovered(
        publication_id="SBER-4-12345",
        ticker="SBER",
        publication_type="report",
        publication_date="2026-04-29",
        source_url="https://www.e-disclosure.ru/portal/FileLoad.ashx?Fileid=12345",
        report_type_code=4,
        report_type_label=(
            "Промежуточная консолидированная финансовая отчетность по МСФО"
        ),
        reporting_period_year=2026,
        reporting_period_type="Q1",
    )
    assert inserted is True
    pub = repo.get_by_id("SBER-4-12345")
    assert pub is not None
    assert pub.report_type_code == 4
    assert pub.report_type_label is not None and "МСФО" in pub.report_type_label
    assert pub.reporting_period_year == 2026
    assert pub.reporting_period_type == "Q1"


def test_upsert_discovered_period_columns_nullable_for_legacy_callers(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    """Backwards compat: callers that don't pass the new kwargs still work."""
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    repo.upsert_discovered(
        publication_id="event-1",
        ticker="SBER",
        publication_type="event",
        publication_date="2026-04-29",
        source_url="https://example/event",
    )
    pub = repo.get_by_id("event-1")
    assert pub is not None
    assert pub.report_type_code is None
    assert pub.report_type_label is None
    assert pub.reporting_period_year is None
    assert pub.reporting_period_type is None


def test_list_by_period_filters_by_year_and_type(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    """Patch 17: list_by_period selects only matching (year, period_type)."""
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    # Two MSFO+RSBU rows for the same Q1 2026 + a stray Q1 2025 row.
    repo.upsert_discovered(
        publication_id="SBER-4-Q1-2026",
        ticker="SBER",
        publication_type="report",
        publication_date="2026-04-29",
        source_url="https://x/4",
        report_type_code=4,
        reporting_period_year=2026,
        reporting_period_type="Q1",
    )
    repo.upsert_discovered(
        publication_id="SBER-3-Q1-2026",
        ticker="SBER",
        publication_type="report",
        publication_date="2026-04-30",
        source_url="https://x/3",
        report_type_code=3,
        reporting_period_year=2026,
        reporting_period_type="Q1",
    )
    repo.upsert_discovered(
        publication_id="SBER-4-Q1-2025",
        ticker="SBER",
        publication_type="report",
        publication_date="2025-04-29",
        source_url="https://x/old",
        report_type_code=4,
        reporting_period_year=2025,
        reporting_period_type="Q1",
    )

    matches = repo.list_by_period("SBER", 2026, "Q1")
    ids = {p.publication_id for p in matches}
    assert ids == {"SBER-4-Q1-2026", "SBER-3-Q1-2026"}

    none_match = repo.list_by_period("SBER", 2026, "FY")
    assert none_match == []


def test_upsert_discovered_rejects_invalid_period_type(
    seeded: tuple[Database, sqlite3.Connection],
) -> None:
    """0007 CHECK constraint guards against bad period_type values."""
    db, conn = seeded
    repo = PublicationsRepo(db, conn)
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert_discovered(
            publication_id="bad",
            ticker="SBER",
            publication_type="report",
            publication_date="2026-04-29",
            source_url="https://x",
            report_type_code=4,
            reporting_period_year=2026,
            reporting_period_type="QX",  # type: ignore[arg-type]
        )
