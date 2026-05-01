"""Google Vision OCR — placeholder for future cloud integration (ТЗ §8)."""

from __future__ import annotations

from pathlib import Path

from edx.stages.text_extractor.models import PageText


class GoogleVisionOCRProvider:
    name = "google_vision"

    def __init__(self, *, endpoint: str | None = None) -> None:
        self.endpoint = endpoint

    def recognize(
        self, pdf_path: Path, langs: list[str]
    ) -> list[PageText]:
        raise NotImplementedError("planned, see config/ocr.yaml")
