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
        tickers_config=settings.tickers,
        raw_dir=settings.app.paths.raw_dir,
        processed_dir=settings.app.paths.processed_dir,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
        completeness_threshold=settings.app.validator.completeness_threshold,
        issuer_trim_max_chars=settings.app.text_extractor.issuer_trim_max_chars,
        issuer_trim_min_section_chars=settings.app.text_extractor.issuer_trim_min_section_chars,
        issuer_trim_toc_distance_chars=settings.app.text_extractor.issuer_trim_toc_distance_chars,
        scan_ratio_threshold=settings.app.metric_extractor.scan_ratio_threshold,
        pdf_input_standards=settings.app.metric_extractor.pdf_input_standards,
        balance_trim_max_chars=settings.app.metric_extractor.balance_trim_max_chars,
        vision_fallback_enabled=settings.app.metric_extractor.vision_fallback_enabled,
        vision_fallback_threshold=settings.app.metric_extractor.vision_fallback_threshold,
        vision_fallback_max_pages=settings.app.metric_extractor.vision_fallback_max_pages,
        vision_only_global_disabled=settings.app.metric_extractor.vision_only_global_disabled,
        vision_only_max_pages_per_request=settings.app.metric_extractor.vision_only_max_pages_per_request,
    )
