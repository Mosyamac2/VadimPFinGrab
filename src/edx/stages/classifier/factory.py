"""Factory for the Classifier stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.stages.classifier.service import ClassifierService
from edx.storage import DocumentsRepo, PublicationsRepo


def build_classifier_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    documents_repo: DocumentsRepo,
) -> ClassifierService:
    cfg = settings.app.classifier
    return ClassifierService(
        publications_repo=publications_repo,
        documents_repo=documents_repo,
        raw_dir=settings.app.paths.raw_dir,
        metrics_config=settings.metrics,
        min_text_chars=cfg.min_text_chars,
        first_pages_to_inspect=cfg.first_pages_to_inspect,
    )
