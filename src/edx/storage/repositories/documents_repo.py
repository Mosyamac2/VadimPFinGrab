"""Documents inside a publication (PDFs, HTML, etc.)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from edx.storage.db import Database
from edx.storage.models import (
    DocumentInput,
    DocumentRow,
    ReportingStandardWithOther,
)


class DocumentsRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    def add_documents(
        self,
        publication_id: str,
        docs: Iterable[DocumentInput],
    ) -> int:
        """Bulk-insert document rows. UNIQUE(publication_id, relative_path) skips dupes."""
        items = list(docs)
        with self.db.transaction(self.conn):
            for doc in items:
                self.conn.execute(
                    """
                    INSERT INTO documents (
                        publication_id, relative_path, mime_type, file_hash
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(publication_id, relative_path) DO UPDATE SET
                        mime_type = excluded.mime_type,
                        file_hash = excluded.file_hash
                    """,
                    (publication_id, doc.relative_path, doc.mime_type, doc.file_hash),
                )
        return len(items)

    def update_classification(
        self,
        document_id: int,
        *,
        reporting_standard: ReportingStandardWithOther | None,
        report_form: str | None,
        is_machine_readable: bool | None,
        page_count: int | None,
    ) -> None:
        with self.db.transaction(self.conn):
            self.conn.execute(
                """
                UPDATE documents
                   SET reporting_standard  = ?,
                       report_form         = ?,
                       is_machine_readable = ?,
                       page_count          = ?
                 WHERE document_id = ?
                """,
                (
                    reporting_standard,
                    report_form,
                    int(is_machine_readable) if is_machine_readable is not None else None,
                    page_count,
                    document_id,
                ),
            )

    def list_for_publication(self, publication_id: str) -> list[DocumentRow]:
        cursor = self.conn.execute(
            "SELECT * FROM documents WHERE publication_id = ? ORDER BY document_id",
            (publication_id,),
        )
        return [_row_to_document(row) for row in cursor]


def _row_to_document(row: sqlite3.Row) -> DocumentRow:
    return DocumentRow(
        document_id=row["document_id"],
        publication_id=row["publication_id"],
        relative_path=row["relative_path"],
        mime_type=row["mime_type"],
        reporting_standard=row["reporting_standard"],
        report_form=row["report_form"],
        is_machine_readable=row["is_machine_readable"],
        page_count=row["page_count"],
        file_hash=row["file_hash"],
    )
