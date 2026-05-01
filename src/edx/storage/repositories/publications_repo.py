"""Publications: discovered scrape items + their lifecycle status."""

from __future__ import annotations

import sqlite3

from edx.storage.db import Database, now_iso
from edx.storage.models import (
    ALLOWED_PUBLICATION_STATUSES,
    PublicationRow,
    PublicationStatus,
    PublicationType,
)


class PublicationsRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    def upsert_discovered(
        self,
        *,
        publication_id: str,
        ticker: str,
        publication_type: PublicationType,
        publication_date: str,
        source_url: str,
    ) -> bool:
        """Insert a freshly-discovered publication. No-op if already known.

        Returns True if a new row was inserted.
        """
        timestamp = now_iso()
        with self.db.transaction(self.conn):
            cursor = self.conn.execute(
                """
                INSERT INTO publications (
                    publication_id, ticker, publication_type, publication_date,
                    source_url, status, discovered_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'discovered', ?, ?)
                ON CONFLICT(publication_id) DO NOTHING
                """,
                (
                    publication_id,
                    ticker,
                    publication_type,
                    publication_date,
                    source_url,
                    timestamp,
                    timestamp,
                ),
            )
            return cursor.rowcount > 0

    def mark_status(
        self,
        publication_id: str,
        status: PublicationStatus,
        error: str | None = None,
        file_hash: str | None = None,
    ) -> None:
        if status not in ALLOWED_PUBLICATION_STATUSES:
            raise ValueError(f"unknown publication status: {status!r}")
        with self.db.transaction(self.conn):
            if file_hash is None:
                self.conn.execute(
                    """
                    UPDATE publications
                       SET status     = ?,
                           last_error = ?,
                           updated_at = ?
                     WHERE publication_id = ?
                    """,
                    (status, error, now_iso(), publication_id),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE publications
                       SET status     = ?,
                           last_error = ?,
                           file_hash  = ?,
                           updated_at = ?
                     WHERE publication_id = ?
                    """,
                    (status, error, file_hash, now_iso(), publication_id),
                )

    def mark_incomplete(
        self, publication_id: str, incomplete: bool = True
    ) -> None:
        """Flag a publication as below the completeness threshold (ТЗ §11.2)."""
        with self.db.transaction(self.conn):
            self.conn.execute(
                """
                UPDATE publications
                   SET is_incomplete = ?, updated_at = ?
                 WHERE publication_id = ?
                """,
                (1 if incomplete else 0, now_iso(), publication_id),
            )

    def get_by_id(self, publication_id: str) -> PublicationRow | None:
        cursor = self.conn.execute(
            "SELECT * FROM publications WHERE publication_id = ?",
            (publication_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_publication(row)

    def latest_publication_date(self, ticker: str) -> str | None:
        cursor = self.conn.execute(
            "SELECT MAX(publication_date) AS d FROM publications WHERE ticker = ?",
            (ticker,),
        )
        row = cursor.fetchone()
        return row["d"] if row and row["d"] is not None else None

    def list_by_status(self, status: PublicationStatus) -> list[PublicationRow]:
        cursor = self.conn.execute(
            "SELECT * FROM publications WHERE status = ? ORDER BY publication_date",
            (status,),
        )
        return [_row_to_publication(row) for row in cursor]


def _row_to_publication(row: sqlite3.Row) -> PublicationRow:
    return PublicationRow(
        publication_id=row["publication_id"],
        ticker=row["ticker"],
        publication_type=row["publication_type"],
        publication_date=row["publication_date"],
        source_url=row["source_url"],
        file_hash=row["file_hash"],
        status=row["status"],
        last_error=row["last_error"],
        discovered_at=row["discovered_at"],
        updated_at=row["updated_at"],
        is_incomplete=row["is_incomplete"],
    )
