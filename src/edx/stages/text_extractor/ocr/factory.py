"""Build the right OCR provider from ``ocr.yaml`` settings."""

from __future__ import annotations

from edx.config import OCRConfig
from edx.stages.text_extractor.ocr.base import OCRProvider
from edx.stages.text_extractor.ocr.google_vision import GoogleVisionOCRProvider
from edx.stages.text_extractor.ocr.tesseract import TesseractOCRProvider
from edx.stages.text_extractor.ocr.yandex_vision import YandexVisionOCRProvider


def build_ocr_provider(ocr_config: OCRConfig) -> OCRProvider:
    engine = ocr_config.engine
    if engine == "tesseract":
        return TesseractOCRProvider(dpi=ocr_config.tesseract_dpi)
    if engine == "yandex_vision":
        return YandexVisionOCRProvider(
            folder_id=ocr_config.yandex_vision.folder_id,
            endpoint=ocr_config.yandex_vision.endpoint,
        )
    if engine == "google_vision":
        return GoogleVisionOCRProvider(
            endpoint=ocr_config.google_vision.endpoint
        )
    raise ValueError(f"unknown OCR engine: {engine!r}")
