"""EventsRepo: idempotent upsert by source_publication_id."""

from __future__ import annotations

import sqlite3

import pytest

from edx.config import TickerEntry
from edx.storage import (
    Database,
    EventInput,
    EventsRepo,
    PublicationsRepo,
    TickersRepo,
)


@pytest.fixture
def seeded(
    tmp_db: Database, conn: sqlite3.Connection
) -> tuple[Database, sqlite3.Connection, str]:
    TickersRepo(tmp_db, conn).upsert_from_config(
        [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
    )
    PublicationsRepo(tmp_db, conn).upsert_discovered(
        publication_id="ev-1", ticker="SBER", publication_type="event",
        publication_date="2026-04-01", source_url="https://example.com/ev1",
    )
    return tmp_db, conn, "ev-1"


def _event(pub_id: str, summary: str = "Hello", event_type: str = "dividends") -> EventInput:
    return EventInput(
        ticker="SBER",
        event_date="2026-04-01",
        publication_date="2026-04-01",
        event_type=event_type,
        summary=summary,
        key_params_json=None,
        source_url="https://example.com/ev1",
        source_publication_id=pub_id,
    )


def test_upsert_inserts_event(
    seeded: tuple[Database, sqlite3.Connection, str],
) -> None:
    db, conn, pub_id = seeded
    repo = EventsRepo(db, conn)
    repo.upsert_event(_event(pub_id))
    row = repo.get_by_publication(pub_id)
    assert row is not None and row.summary == "Hello"


def test_upsert_idempotent_updates(
    seeded: tuple[Database, sqlite3.Connection, str],
) -> None:
    db, conn, pub_id = seeded
    repo = EventsRepo(db, conn)
    repo.upsert_event(_event(pub_id, summary="first", event_type="other"))
    repo.upsert_event(_event(pub_id, summary="second", event_type="dividends"))
    row = repo.get_by_publication(pub_id)
    assert row is not None
    assert row.summary == "second"
    assert row.event_type == "dividends"
    cursor = conn.execute("SELECT COUNT(*) AS c FROM events")
    assert cursor.fetchone()["c"] == 1
