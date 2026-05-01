"""Issuers (``tickers`` table)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from edx.config import TickerEntry
from edx.storage.db import Database, now_iso
from edx.storage.models import TickerRow


class TickersRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    def upsert_from_config(self, entries: Iterable[TickerEntry]) -> int:
        """Insert or update ticker rows; returns the number of entries processed.

        ``added_at`` is preserved on update (only set on first insert).
        """
        rows = list(entries)
        with self.db.transaction(self.conn):
            for entry in rows:
                self.conn.execute(
                    """
                    INSERT INTO tickers (
                        ticker, e_disclosure_id, inn, ogrn, name, added_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        e_disclosure_id = excluded.e_disclosure_id,
                        inn             = excluded.inn,
                        ogrn            = excluded.ogrn,
                        name            = excluded.name
                    """,
                    (
                        entry.ticker,
                        entry.e_disclosure_id,
                        entry.inn,
                        entry.ogrn,
                        entry.name,
                        now_iso(),
                    ),
                )
        return len(rows)

    def list_active(self) -> list[TickerRow]:
        cursor = self.conn.execute(
            "SELECT ticker, e_disclosure_id, inn, ogrn, name, added_at "
            "FROM tickers ORDER BY ticker"
        )
        return [
            TickerRow(
                ticker=row["ticker"],
                e_disclosure_id=row["e_disclosure_id"],
                inn=row["inn"],
                ogrn=row["ogrn"],
                name=row["name"],
                added_at=row["added_at"],
            )
            for row in cursor
        ]
