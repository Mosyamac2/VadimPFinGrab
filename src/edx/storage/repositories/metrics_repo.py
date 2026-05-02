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

        Patch 27: uses ``INSERT OR REPLACE`` so a row that collides with
        the UNIQUE(ticker, reporting_date, period_type, reporting_standard,
        metric_name) key from *another* publication doesn't crash the
        whole publication. The collision is real on e-disclosure: an
        IFRS Q1 report routinely carries a ``comparative prior``
        2024-FY block alongside the current Q1 numbers; if another
        publication already wrote those 2024-FY rows, a strict INSERT
        would crash the entire publication. With OR REPLACE the
        most-recently-extracted row wins; the source_document_id is
        also updated so the Excel mart shows the latest source URL.

        If any insert fails for *other* reasons (CHECK violation,
        type mismatch), the transaction is rolled back and the
        original metrics remain untouched.
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
                    INSERT OR REPLACE INTO metrics (
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

    def list_all_for_export(self) -> list[dict[str, object]]:
        """Return one dict per metric row, joined to its publication's source URL.

        Used by the Writer stage to build the Excel mart. Sorted for stable
        deterministic output.
        """
        cursor = self.conn.execute(
            """
            SELECT
                m.metric_id              AS metric_id,
                m.ticker                 AS ticker,
                m.reporting_date         AS reporting_date,
                m.period_type            AS period_type,
                m.reporting_standard     AS reporting_standard,
                m.metric_name            AS metric_name,
                m.value                  AS value,
                m.currency               AS currency,
                m.unit                   AS unit,
                m.qa_warning             AS qa_warning,
                p.source_url             AS source_publication_url
            FROM metrics m
            JOIN documents d ON d.document_id = m.source_document_id
            JOIN publications p ON p.publication_id = d.publication_id
            ORDER BY m.ticker, m.reporting_date, m.period_type, m.metric_name
            """
        )
        return [dict(row) for row in cursor]

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
