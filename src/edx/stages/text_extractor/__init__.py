"""Text Extractor stage: native pymupdf + pdfplumber, plus pluggable OCR."""

from edx.stages.text_extractor.factory import build_text_extractor_service
from edx.stages.text_extractor.models import PageText
from edx.stages.text_extractor.normalize import normalize_text
from edx.stages.text_extractor.service import (
    ExtractOutcome,
    TextExtractorService,
)

__all__ = [
    "ExtractOutcome",
    "PageText",
    "TextExtractorService",
    "build_text_extractor_service",
    "normalize_text",
]
