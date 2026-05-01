"""WriterService: assemble snapshot from state.sqlite, write Excel mart."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from edx import __version__
from edx.logging_setup import get_logger
from edx.stages.writer.excel import (
    EventExportRow,
    ExcelWriter,
    MetaSnapshot,
    MetricExportRow,
    QAIssueExportRow,
    WitrineSnapshot,
)
from edx.storage import (
    EventsRepo,
    MetricsRepo,
    PublicationsRepo,
    QAIssuesRepo,
    TickersRepo,
)


class WriterService:
    """Reads aggregates from repos, writes the Excel mart, marks publications written."""

    def __init__(
        self,
        publications_repo: PublicationsRepo,
        metrics_repo: MetricsRepo,
        events_repo: EventsRepo,
        qa_issues_repo: QAIssuesRepo,
        tickers_repo: TickersRepo,
        *,
        excel_path: Path,
        excel_writer: ExcelWriter | None = None,
    ) -> None:
        self.publications_repo = publications_repo
        self.metrics_repo = metrics_repo
        self.events_repo = events_repo
        self.qa_issues_repo = qa_issues_repo
        self.tickers_repo = tickers_repo
        self.excel_path = Path(excel_path)
        self.excel_writer = excel_writer or ExcelWriter()
        self._log = get_logger("edx.stages.writer")

    def run(self) -> Path:
        snapshot = self._build_snapshot()
        self.excel_writer.write(self.excel_path, snapshot)
        marked = self._mark_validated_as_written()
        self._log.info(
            "writer_excel_emitted",
            path=str(self.excel_path),
            metrics_rows=len(snapshot.metrics),
            events_rows=len(snapshot.events),
            qa_issues_rows=len(snapshot.qa_issues),
            publications_marked_written=marked,
        )
        return self.excel_path

    def _build_snapshot(self) -> WitrineSnapshot:
        metric_rows: list[MetricExportRow] = []
        for r in self.metrics_repo.list_all_for_export():
            raw_value = r["value"]
            value: float | None = (
                None
                if raw_value is None
                else float(raw_value)  # type: ignore[arg-type]
            )
            metric_rows.append(
                MetricExportRow(
                    ticker=str(r["ticker"]),
                    reporting_date=str(r["reporting_date"]),
                    period_type=str(r["period_type"]),
                    reporting_standard=str(r["reporting_standard"]),
                    metric_name=str(r["metric_name"]),
                    value=value,
                    currency=str(r["currency"]),
                    unit=str(r["unit"]),
                    qa_warning=(
                        None
                        if r["qa_warning"] is None
                        else str(r["qa_warning"])
                    ),
                    source_publication_url=str(r["source_publication_url"]),
                )
            )
        event_rows = [
            EventExportRow(
                ticker=ev.ticker,
                event_date=ev.event_date,
                publication_date=ev.publication_date,
                event_type=ev.event_type,
                summary=ev.summary,
                key_params_json=ev.key_params_json,
                source_url=ev.source_url,
            )
            for ev in self.events_repo.list_all_for_export()
        ]
        qa_rows = [
            QAIssueExportRow(
                ticker=issue.ticker,
                publication_id=issue.publication_id,
                code=issue.code,
                message=issue.message,
                created_at=issue.created_at,
            )
            for issue in self.qa_issues_repo.list_all()
        ]

        meta = MetaSnapshot(
            last_updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            pipeline_version=__version__,
            tickers_count=len(self.tickers_repo.list_active()),
            metrics_rows=len(metric_rows),
            events_rows=len(event_rows),
            incomplete_publications=self._count_incomplete(),
            failed_publications=len(
                self.publications_repo.list_by_status("failed")
            ),
        )
        return WitrineSnapshot(
            metrics=metric_rows,
            events=event_rows,
            qa_issues=qa_rows,
            meta=meta,
        )

    def _mark_validated_as_written(self) -> int:
        validated = self.publications_repo.list_by_status("validated")
        for pub in validated:
            self.publications_repo.mark_status(pub.publication_id, "written")
        return len(validated)

    def _count_incomplete(self) -> int:
        cursor = self.publications_repo.conn.execute(
            "SELECT COUNT(*) AS c FROM publications WHERE is_incomplete = 1"
        )
        row = cursor.fetchone()
        return int(row["c"]) if row else 0
