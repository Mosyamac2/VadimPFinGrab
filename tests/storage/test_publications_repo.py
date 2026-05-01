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
