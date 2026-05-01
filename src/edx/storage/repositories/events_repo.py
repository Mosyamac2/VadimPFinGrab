"""Material events extracted from publications."""

from __future__ import annotations

import sqlite3

from edx.storage.db import Database, now_iso
from edx.storage.models import EventInput, EventRow


class EventsRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    def upsert_event(self, event: EventInput) -> None:
        """Idempotent on ``source_publication_id`` (UNIQUE)."""
        with self.db.transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO events (
                    ticker, event_date, publication_date, event_type, summary,
                    key_params_json, source_url, source_publication_id, extracted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_publication_id) DO UPDATE SET
                    ticker           = excluded.ticker,
                    event_date       = excluded.event_date,
                    publication_date = excluded.publication_date,
                    event_type       = excluded.event_type,
                    summary          = excluded.summary,
                    key_params_json  = excluded.key_params_json,
                    source_url       = excluded.source_url,
                    extracted_at     = excluded.extracted_at
                """,
                (
                    event.ticker,
                    event.event_date,
                    event.publication_date,
                    event.event_type,
                    event.summary,
                    event.key_params_json,
                    event.source_url,
                    event.source_publication_id,
                    now_iso(),
                ),
            )

    def list_all_for_export(self) -> list[EventRow]:
        """All events sorted by ``event_date`` desc for the Excel mart."""
        cursor = self.conn.execute(
            "SELECT * FROM events ORDER BY event_date DESC, event_id"
        )
        return [_row_to_event(row) for row in cursor]

    def get_by_publication(self, publication_id: str) -> EventRow | None:
        cursor = self.conn.execute(
            "SELECT * FROM events WHERE source_publication_id = ?",
            (publication_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_event(row)


def _row_to_event(row: sqlite3.Row) -> EventRow:
    return EventRow(
        event_id=row["event_id"],
        ticker=row["ticker"],
        event_date=row["event_date"],
        publication_date=row["publication_date"],
        event_type=row["event_type"],
        summary=row["summary"],
        key_params_json=row["key_params_json"],
        source_url=row["source_url"],
        source_publication_id=row["source_publication_id"],
        extracted_at=row["extracted_at"],
    )
