"""Factory for the Validator stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.stages.validator.service import ValidatorService
from edx.storage import MetricsRepo, PublicationsRepo, QAIssuesRepo


def build_validator_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    metrics_repo: MetricsRepo,
    qa_issues_repo: QAIssuesRepo,
) -> ValidatorService:
    return ValidatorService(
        publications_repo=publications_repo,
        metrics_repo=metrics_repo,
        qa_issues_repo=qa_issues_repo,
        completeness_threshold=settings.app.validator.completeness_threshold,
        metrics_per_period=len(settings.metrics.metrics),
    )
