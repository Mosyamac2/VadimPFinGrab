"""QA issues — aggregated report of validation warnings (ТЗ §11)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from edx.storage.db import Database, now_iso
from edx.storage.models import QAIssueRow


class QAIssuesRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    def replace_for_publication(
        self,
        publication_id: str,
        ticker: str,
        issues: Iterable[tuple[str, str]],
    ) -> int:
        """Atomically rewrite issues for a publication.

        ``issues`` is an iterable of ``(code, message)`` pairs. Re-running the
        Validator over the same publication produces the same set of rows
        without duplicates.
        """
        rows = list(issues)
        timestamp = now_iso()
        with self.db.transaction(self.conn):
            self.conn.execute(
                "DELETE FROM qa_issues WHERE publication_id = ?",
                (publication_id,),
            )
            for code, message in rows:
                self.conn.execute(
                    """
                    INSERT INTO qa_issues
                        (publication_id, ticker, code, message, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (publication_id, ticker, code, message, timestamp),
                )
        return len(rows)

    def list_for_publication(
        self, publication_id: str
    ) -> list[QAIssueRow]:
        cursor = self.conn.execute(
            "SELECT * FROM qa_issues WHERE publication_id = ? ORDER BY issue_id",
            (publication_id,),
        )
        return [_row(row) for row in cursor]

    def list_all(self) -> list[QAIssueRow]:
        cursor = self.conn.execute(
            "SELECT * FROM qa_issues ORDER BY created_at DESC, issue_id"
        )
        return [_row(row) for row in cursor]


def _row(row: sqlite3.Row) -> QAIssueRow:
    return QAIssueRow(
        issue_id=row["issue_id"],
        publication_id=row["publication_id"],
        ticker=row["ticker"],
        code=row["code"],
        message=row["message"],
        created_at=row["created_at"],
    )
