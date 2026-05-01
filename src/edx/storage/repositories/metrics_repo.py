"""Extracted financial metrics."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from edx.storage.db import Database, now_iso
from edx.storage.models import MetricInput, MetricRow


class MetricsRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    def replace_for_publication(
        self,
        publication_id: str,
        rows: Iterable[MetricInput],
    ) -> int:
        """Atomically delete all metrics linked to ``publication_id`` and insert ``rows``.

        If any insert fails (e.g. CHECK violation), the transaction is rolled back
        and the original metrics remain untouched.
        """
        new_rows = list(rows)
        timestamp = now_iso()
        with self.db.transaction(self.conn):
            self.conn.execute(
                """
                DELETE FROM metrics
                 WHERE source_document_id IN (
                       SELECT document_id FROM documents WHERE publication_id = ?
                 )
                """,
                (publication_id,),
            )
            for row in new_rows:
                self.conn.execute(
                    """
                    INSERT INTO metrics (
                        ticker, reporting_date, period_type, reporting_standard,
                        metric_name, value, currency, unit, source_document_id,
                        qa_warning, extracted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.ticker,
                        row.reporting_date,
                        row.period_type,
                        row.reporting_standard,
                        row.metric_name,
                        row.value,
                        row.currency,
                        row.unit,
                        row.source_document_id,
                        row.qa_warning,
                        timestamp,
                    ),
                )
        return len(new_rows)

    def list_for_publication(self, publication_id: str) -> list[MetricRow]:
        cursor = self.conn.execute(
            """
            SELECT m.* FROM metrics m
            JOIN documents d ON d.document_id = m.source_document_id
            WHERE d.publication_id = ?
            ORDER BY m.metric_id
            """,
            (publication_id,),
        )
        return [_row_to_metric(row) for row in cursor]

    def list_for_ticker(self, ticker: str) -> list[MetricRow]:
        cursor = self.conn.execute(
            "SELECT * FROM metrics WHERE ticker = ? "
            "ORDER BY reporting_date, period_type, metric_name",
            (ticker,),
        )
        return [_row_to_metric(row) for row in cursor]

    def update_qa_warning(
        self, metric_id: int, qa_warning: str | None
    ) -> None:
        with self.db.transaction(self.conn):
            self.conn.execute(
                "UPDATE metrics SET qa_warning = ? WHERE metric_id = ?",
                (qa_warning, metric_id),
            )


def _row_to_metric(row: sqlite3.Row) -> MetricRow:
    return MetricRow(
        metric_id=row["metric_id"],
        ticker=row["ticker"],
        reporting_date=row["reporting_date"],
        period_type=row["period_type"],
        reporting_standard=row["reporting_standard"],
        metric_name=row["metric_name"],
        value=row["value"],
        currency=row["currency"],
        unit=row["unit"],
        source_document_id=row["source_document_id"],
        qa_warning=row["qa_warning"],
        extracted_at=row["extracted_at"],
    )
