"""TickersRepo: upsert + list."""

from __future__ import annotations

import sqlite3

from edx.config import TickerEntry
from edx.storage import Database, TickersRepo


def test_upsert_inserts_new_rows(tmp_db: Database, conn: sqlite3.Connection) -> None:
    repo = TickersRepo(tmp_db, conn)
    inserted = repo.upsert_from_config(
        [
            TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank"),
            TickerEntry(ticker="GAZP", e_disclosure_id="2", name="Gazprom"),
        ]
    )
    assert inserted == 2
    rows = repo.list_active()
    assert [r.ticker for r in rows] == ["GAZP", "SBER"]


def test_upsert_idempotent_updates_name(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = TickersRepo(tmp_db, conn)
    repo.upsert_from_config(
        [TickerEntry(ticker="LKOH", e_disclosure_id="3", name="Lukoil")]
    )
    repo.upsert_from_config(
        [TickerEntry(ticker="LKOH", e_disclosure_id="3", name="ПАО ЛУКОЙЛ")]
    )
    rows = repo.list_active()
    assert len(rows) == 1
    assert rows[0].name == "ПАО ЛУКОЙЛ"


def test_upsert_preserves_added_at(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = TickersRepo(tmp_db, conn)
    repo.upsert_from_config(
        [TickerEntry(ticker="ROSN", e_disclosure_id="9", name="Rosneft")]
    )
    original = repo.list_active()[0].added_at
    repo.upsert_from_config(
        [TickerEntry(ticker="ROSN", e_disclosure_id="9", name="Rosneft (renamed)")]
    )
    after = repo.list_active()[0].added_at
    assert original == after
