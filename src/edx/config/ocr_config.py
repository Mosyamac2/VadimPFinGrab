"""OCR engine configuration. Defaults to local Tesseract (ТЗ §8)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OCREngine = Literal["tesseract", "yandex_vision", "google_vision"]


class YandexVisionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_id: str | None = None
    endpoint: str = "https://ocr.api.cloud.yandex.net"


class GoogleVisionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = "https://vision.googleapis.com"


class OCRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: OCREngine = "tesseract"
    tesseract_langs: list[str] = Field(default_factory=lambda: ["rus", "eng"])
    # Patch 31: 400 DPI on Russian RSBU forms is materially better than
    # 300 — the right-hand columns ("На 31 декабря YYYY") of bank balance
    # forms confuse 8↔3, 0↔O at 300. Costs ~+50% CPU per page.
    tesseract_dpi: int = Field(default=400, ge=72, le=1200)
    # Patch 31: PSM 6 = "single uniform block of text" — the recommended
    # mode for tabular forms with thin grid lines (РСБУ 0710001/0710002).
    # Tesseract's default PSM 3 (auto layout) routinely splits balance
    # rows mid-cell and drops digits.
    tesseract_psm: int = Field(default=6, ge=0, le=13)
    # Patch 31: when the primary PSM yields very little text or almost no
    # digits (cover/title pages, empty annexes), retry with this PSM and
    # keep the longer output. ``null`` disables the retry. PSM 4 = single
    # column of variable-sized text — works well on cover-page layouts.
    tesseract_retry_psm: int | None = Field(default=4)
    tesseract_retry_min_chars: int = Field(default=80, ge=0)
    tesseract_retry_min_digit_ratio: float = Field(default=0.05, ge=0.0, le=1.0)
    yandex_vision: YandexVisionOptions = Field(default_factory=YandexVisionOptions)
    google_vision: GoogleVisionOptions = Field(default_factory=GoogleVisionOptions)
