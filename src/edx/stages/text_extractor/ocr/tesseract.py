"""Local Tesseract OCR via pytesseract + pdf2image."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path

from edx.stages.text_extractor.models import PageText


class TesseractOCRMissingError(RuntimeError):
    """Raised when the ``tesseract`` binary is not on PATH at recognition time."""


class TesseractOCRProvider:
    """Locally-running Tesseract. Bills nothing; ships nothing offsite."""

    name = "tesseract"

    def __init__(self, *, dpi: int = 300) -> None:
        self.dpi = dpi

    def recognize(
        self, pdf_path: Path, langs: list[str]
    ) -> list[PageText]:
        if shutil.which("tesseract") is None:
            raise TesseractOCRMissingError(
                "tesseract binary not found on PATH; install tesseract-ocr"
            )
        # pdf2image needs poppler-utils on PATH; an absence raises a
        # PDFInfoNotInstalledError which we let propagate (the service layer
        # logs and marks the publication failed).
        images = convert_from_path(str(pdf_path), dpi=self.dpi)
        lang_arg = "+".join(langs) if langs else "eng"

        pages: list[PageText] = []
        for index, image in enumerate(images):
            text = pytesseract.image_to_string(image, lang=lang_arg) or ""
            pages.append(PageText(page_number=index + 1, text=text))
        return pages
