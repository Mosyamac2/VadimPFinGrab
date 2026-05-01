"""OCR providers for the Text Extractor stage."""

from edx.stages.text_extractor.ocr.base import OCRProvider
from edx.stages.text_extractor.ocr.factory import build_ocr_provider
from edx.stages.text_extractor.ocr.google_vision import GoogleVisionOCRProvider
from edx.stages.text_extractor.ocr.tesseract import TesseractOCRProvider
from edx.stages.text_extractor.ocr.yandex_vision import YandexVisionOCRProvider

__all__ = [
    "GoogleVisionOCRProvider",
    "OCRProvider",
    "TesseractOCRProvider",
    "YandexVisionOCRProvider",
    "build_ocr_provider",
]
