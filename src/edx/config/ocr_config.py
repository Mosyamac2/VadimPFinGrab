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
    tesseract_dpi: int = Field(default=300, ge=72, le=1200)
    yandex_vision: YandexVisionOptions = Field(default_factory=YandexVisionOptions)
    google_vision: GoogleVisionOptions = Field(default_factory=GoogleVisionOptions)
