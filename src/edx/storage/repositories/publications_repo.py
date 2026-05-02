"""Publications: discovered scrape items + their lifecycle status."""

from __future__ import annotations

import sqlite3

from edx.storage.db import Database, now_iso
from edx.storage.models import (
    ALLOWED_PUBLICATION_STATUSES,
    PeriodType,
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
        report_type_code: int | None = None,
        report_type_label: str | None = None,
        reporting_period_year: int | None = None,
        reporting_period_type: PeriodType | None = None,
    ) -> bool:
        """Insert a freshly-discovered publication. No-op if already known.

        Returns True if a new row was inserted.

        ``report_type_code`` / ``report_type_label`` / ``reporting_period_*``
        are filled by the Discoverer (Patch 16) directly from the listing page
        URL and table; they remain ``None`` for ``publication_type='event'``
        and for legacy rows from before Patch 17.
        """
        timestamp = now_iso()
        with self.db.transaction(self.conn):
            cursor = self.conn.execute(
                """
                INSERT INTO publications (
                    publication_id, ticker, publication_type, publication_date,
                    source_url, status, discovered_at, updated_at,
                    report_type_code, report_type_label,
                    reporting_period_year, reporting_period_type
                ) VALUES (?, ?, ?, ?, ?, 'discovered', ?, ?, ?, ?, ?, ?)
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
                    report_type_code,
                    report_type_label,
                    reporting_period_year,
                    reporting_period_type,
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

    def reset_status_to_discovered_since(self, cutoff_date: str) -> int:
        """Force every publication on/after ``cutoff_date`` back to ``discovered``.

        Used by ``Orchestrator.run("full_reload")``. Files on disk are kept
        — the Downloader/Unpacker will see matching hashes and skip the local
        steps. LLM-stages re-fire only if ``data/processed/_llm_cache`` is
        cleared separately.
        """
        with self.db.transaction(self.conn):
            cursor = self.conn.execute(
                """
                UPDATE publications
                   SET status     = 'discovered',
                       last_error = NULL,
                       updated_at = ?
                 WHERE publication_date >= ?
                """,
                (now_iso(), cutoff_date),
            )
            return cursor.rowcount

    def list_by_status(self, status: PublicationStatus) -> list[PublicationRow]:
        cursor = self.conn.execute(
            "SELECT * FROM publications WHERE status = ? ORDER BY publication_date",
            (status,),
        )
        return [_row_to_publication(row) for row in cursor]

    def list_all(self) -> list[PublicationRow]:
        cursor = self.conn.execute(
            "SELECT * FROM publications ORDER BY publication_date"
        )
        return [_row_to_publication(row) for row in cursor]

    def list_by_period(
        self,
        ticker: str,
        reporting_period_year: int,
        reporting_period_type: PeriodType,
    ) -> list[PublicationRow]:
        """Return every publication of this ticker for the given period.

        Used by the Metric Extractor (Patch 19/21) to pick the best source
        among IFRS / RSBU / ISSUER for the same reporting period.
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM publications
             WHERE ticker = ?
               AND reporting_period_year = ?
               AND reporting_period_type = ?
             ORDER BY publication_date DESC
            """,
            (ticker, reporting_period_year, reporting_period_type),
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
        report_type_code=row["report_type_code"],
        report_type_label=row["report_type_label"],
        reporting_period_year=row["reporting_period_year"],
        reporting_period_type=row["reporting_period_type"],
    )
