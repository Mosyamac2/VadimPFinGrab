"""Factory for the Metric Extractor stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.providers.llm import LLMProvider
from edx.stages.metric_extractor.service import MetricExtractorService
from edx.storage import DocumentsRepo, MetricsRepo, PublicationsRepo


def build_metric_extractor_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    documents_repo: DocumentsRepo,
    metrics_repo: MetricsRepo,
    llm_provider: LLMProvider,
) -> MetricExtractorService:
    return MetricExtractorService(
        llm_provider=llm_provider,
        publications_repo=publications_repo,
        documents_repo=documents_repo,
        metrics_repo=metrics_repo,
        metrics_config=settings.metrics,
        raw_dir=settings.app.paths.raw_dir,
        processed_dir=settings.app.paths.processed_dir,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
        completeness_threshold=settings.app.validator.completeness_threshold,
    )
