"""Factory for the Text Extractor stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.stages.text_extractor.ocr.base import OCRProvider
from edx.stages.text_extractor.ocr.factory import build_ocr_provider
from edx.stages.text_extractor.service import TextExtractorService
from edx.storage import DocumentsRepo, PublicationsRepo


def build_text_extractor_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    documents_repo: DocumentsRepo,
    *,
    ocr_provider: OCRProvider | None = None,
) -> TextExtractorService:
    cfg = settings.app.text_extractor
    provider = ocr_provider or build_ocr_provider(settings.ocr)
    return TextExtractorService(
        publications_repo=publications_repo,
        documents_repo=documents_repo,
        ocr_provider=provider,
        raw_dir=settings.app.paths.raw_dir,
        processed_dir=settings.app.paths.processed_dir,
        ocr_langs=list(settings.ocr.tesseract_langs),
        max_chars=cfg.max_chars,
        extract_tables_enabled=cfg.extract_tables,
        header_footer_min_pages=cfg.header_footer_min_pages,
    )
