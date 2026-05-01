"""OCR provider tests: Tesseract sanity (skipped without binary) + cloud stubs."""

from __future__ import annotations

from pathlib import Path

import pytest

from edx.config import OCRConfig
from edx.stages.text_extractor.ocr import (
    GoogleVisionOCRProvider,
    TesseractOCRProvider,
    YandexVisionOCRProvider,
    build_ocr_provider,
)
from edx.stages.text_extractor.ocr.tesseract import TesseractOCRMissingError
from tests.stages.text_extractor.conftest import requires_tesseract


def test_factory_returns_tesseract_for_default_config() -> None:
    provider = build_ocr_provider(OCRConfig())
    assert isinstance(provider, TesseractOCRProvider)
    assert provider.name == "tesseract"


def test_factory_returns_yandex_vision() -> None:
    provider = build_ocr_provider(OCRConfig.model_validate({"engine": "yandex_vision"}))
    assert isinstance(provider, YandexVisionOCRProvider)
    assert provider.name == "yandex_vision"


def test_factory_returns_google_vision() -> None:
    provider = build_ocr_provider(OCRConfig.model_validate({"engine": "google_vision"}))
    assert isinstance(provider, GoogleVisionOCRProvider)
    assert provider.name == "google_vision"


def test_yandex_vision_recognize_raises_not_implemented(tmp_path: Path) -> None:
    provider = YandexVisionOCRProvider()
    with pytest.raises(NotImplementedError, match="planned"):
        provider.recognize(tmp_path / "any.pdf", ["rus"])


def test_google_vision_recognize_raises_not_implemented(tmp_path: Path) -> None:
    provider = GoogleVisionOCRProvider()
    with pytest.raises(NotImplementedError, match="planned"):
        provider.recognize(tmp_path / "any.pdf", ["rus"])


def test_tesseract_missing_binary_raises_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``recognize`` must surface a clear error when tesseract is absent."""
    import edx.stages.text_extractor.ocr.tesseract as tesseract_module

    monkeypatch.setattr(tesseract_module.shutil, "which", lambda _: None)
    provider = TesseractOCRProvider(dpi=150)
    with pytest.raises(TesseractOCRMissingError):
        provider.recognize(tmp_path / "x.pdf", ["eng"])


@requires_tesseract
def test_tesseract_recognises_synthetic_png(tmp_path: Path) -> None:
    """Sanity: pytesseract returns the known string from a Pillow-rendered PNG."""
    import pytesseract
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=48)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((30, 60), "HELLO OCR", font=font, fill="black")
    target = tmp_path / "hello.png"
    img.save(target)

    text = pytesseract.image_to_string(str(target), lang="eng")
    assert "HELLO" in text.upper()
