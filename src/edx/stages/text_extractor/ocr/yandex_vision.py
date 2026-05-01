"""Yandex Vision OCR — placeholder for future cloud integration (ТЗ §8)."""

from __future__ import annotations

from pathlib import Path

from edx.stages.text_extractor.models import PageText


class YandexVisionOCRProvider:
    name = "yandex_vision"

    def __init__(self, *, folder_id: str | None = None, endpoint: str | None = None) -> None:
        self.folder_id = folder_id
        self.endpoint = endpoint

    def recognize(
        self, pdf_path: Path, langs: list[str]
    ) -> list[PageText]:
        raise NotImplementedError("planned, see config/ocr.yaml")
