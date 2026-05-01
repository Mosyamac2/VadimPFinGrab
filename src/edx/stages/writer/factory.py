"""Factory for the Writer stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.stages.writer.service import WriterService
from edx.storage import (
    EventsRepo,
    MetricsRepo,
    PublicationsRepo,
    QAIssuesRepo,
    TickersRepo,
)


def build_writer_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    metrics_repo: MetricsRepo,
    events_repo: EventsRepo,
    qa_issues_repo: QAIssuesRepo,
    tickers_repo: TickersRepo,
) -> WriterService:
    return WriterService(
        publications_repo=publications_repo,
        metrics_repo=metrics_repo,
        events_repo=events_repo,
        qa_issues_repo=qa_issues_repo,
        tickers_repo=tickers_repo,
        excel_path=settings.app.paths.excel_path,
    )
