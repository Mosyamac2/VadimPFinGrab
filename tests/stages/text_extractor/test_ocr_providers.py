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


# ---------------------------------------------------------------------------
# Patch 31: DPI 400 + PSM 6 default + per-page retry on low text/digits
# ---------------------------------------------------------------------------


def test_default_construction_uses_psm_6_dpi_400() -> None:
    """Patch 31 changes Tesseract defaults: 400 DPI, PSM 6, retry PSM 4."""
    provider = TesseractOCRProvider()
    assert provider.dpi == 400
    assert provider.psm == 6
    assert provider.retry_psm == 4
    assert provider.retry_min_chars == 80
    assert provider.retry_min_digit_ratio == 0.05


def test_factory_propagates_psm_and_retry_knobs() -> None:
    cfg = OCRConfig.model_validate(
        {
            "engine": "tesseract",
            "tesseract_dpi": 600,
            "tesseract_psm": 11,
            "tesseract_retry_psm": None,
            "tesseract_retry_min_chars": 200,
            "tesseract_retry_min_digit_ratio": 0.20,
        }
    )
    provider = build_ocr_provider(cfg)
    assert isinstance(provider, TesseractOCRProvider)
    assert provider.dpi == 600
    assert provider.psm == 11
    assert provider.retry_psm is None
    assert provider.retry_min_chars == 200
    assert provider.retry_min_digit_ratio == 0.20


def test_run_once_passes_psm_to_pytesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--psm N`` must reach pytesseract.image_to_string config."""
    import edx.stages.text_extractor.ocr.tesseract as tesseract_module

    captured: dict[str, object] = {}

    def fake_image_to_string(image, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(
        tesseract_module.pytesseract, "image_to_string", fake_image_to_string
    )
    provider = TesseractOCRProvider(psm=6)
    out = provider._run_once(object(), "rus+eng", 6)
    assert out == "ok"
    assert captured["config"] == "--psm 6"
    assert captured["lang"] == "rus+eng"


def test_needs_retry_short_text_triggers() -> None:
    provider = TesseractOCRProvider(retry_min_chars=80)
    assert provider._needs_retry("a" * 20) is True


def test_needs_retry_low_digit_ratio_triggers() -> None:
    provider = TesseractOCRProvider(retry_min_digit_ratio=0.05)
    # 200-char Russian text without digits → digit_ratio = 0 → retry.
    assert provider._needs_retry("русский текст без цифр совсем нет " * 10) is True


def test_needs_retry_passing_text_does_not_trigger() -> None:
    provider = TesseractOCRProvider(
        retry_min_chars=80, retry_min_digit_ratio=0.05
    )
    # 200 chars, ~30 digits → ratio 0.15 > 0.05 → no retry.
    text = "Активы 397216398 Запасы 73327449 Баланс 846546320 " * 4
    assert provider._needs_retry(text) is False


def test_retry_chosen_when_better(monkeypatch: pytest.MonkeyPatch) -> None:
    """Primary returns 50 chars, retry returns 200 → final = retry."""
    import edx.stages.text_extractor.ocr.tesseract as tesseract_module

    primary = "x" * 50
    retry = "y" * 200

    def fake_image_to_string(_image, *, lang, config):  # type: ignore[no-untyped-def]
        return retry if "--psm 4" in config else primary

    monkeypatch.setattr(
        tesseract_module.pytesseract,
        "image_to_string",
        fake_image_to_string,
    )
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda _: "/bin/x")
    monkeypatch.setattr(
        tesseract_module,
        "convert_from_path",
        lambda _path, dpi: [object()],  # one fake image
    )

    provider = TesseractOCRProvider(
        psm=6, retry_psm=4, retry_min_chars=100, retry_min_digit_ratio=0.0
    )
    pages = provider.recognize(Path("/fake.pdf"), ["rus"])
    assert pages[0].text == retry


def test_retry_not_chosen_when_not_better(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary returns 50 chars, retry returns 30 → keep primary."""
    import edx.stages.text_extractor.ocr.tesseract as tesseract_module

    primary = "x" * 50
    retry = "y" * 30

    def fake_image_to_string(_image, *, lang, config):  # type: ignore[no-untyped-def]
        return retry if "--psm 4" in config else primary

    monkeypatch.setattr(
        tesseract_module.pytesseract,
        "image_to_string",
        fake_image_to_string,
    )
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda _: "/bin/x")
    monkeypatch.setattr(
        tesseract_module,
        "convert_from_path",
        lambda _path, dpi: [object()],
    )

    provider = TesseractOCRProvider(
        psm=6, retry_psm=4, retry_min_chars=100, retry_min_digit_ratio=0.0
    )
    pages = provider.recognize(Path("/fake.pdf"), ["rus"])
    assert pages[0].text == primary


def test_retry_disabled_when_retry_psm_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retry_psm=None → even bad primary output isn't retried."""
    import edx.stages.text_extractor.ocr.tesseract as tesseract_module

    calls: list[str] = []

    def fake_image_to_string(_image, *, lang, config):  # type: ignore[no-untyped-def]
        calls.append(config)
        return "bad"  # short → would trigger retry

    monkeypatch.setattr(
        tesseract_module.pytesseract,
        "image_to_string",
        fake_image_to_string,
    )
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda _: "/bin/x")
    monkeypatch.setattr(
        tesseract_module,
        "convert_from_path",
        lambda _path, dpi: [object()],
    )

    provider = TesseractOCRProvider(psm=6, retry_psm=None)
    provider.recognize(Path("/fake.pdf"), ["rus"])
    assert calls == ["--psm 6"]  # exactly one call, no retry


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
