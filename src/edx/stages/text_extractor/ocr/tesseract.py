"""Local Tesseract OCR via pytesseract + pdf2image.

Patch 31: bumps default DPI to 400 and PSM to 6 (single uniform block
of text) which both materially improve number recognition on Russian
RSBU balance/P&L forms with thin grid lines. Adds an optional per-page
retry with a different PSM when the primary pass yields very little
text or almost no digits — covers cover pages and title sheets where
PSM 6 over-segments.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path, pdfinfo_from_path

from edx.logging_setup import get_logger
from edx.stages.text_extractor.models import PageText


class TesseractOCRMissingError(RuntimeError):
    """Raised when the ``tesseract`` binary is not on PATH at recognition time."""


class TesseractOCRProvider:
    """Locally-running Tesseract. Bills nothing; ships nothing offsite."""

    name = "tesseract"

    def __init__(
        self,
        *,
        dpi: int = 400,
        psm: int = 6,
        retry_psm: int | None = 4,
        retry_min_chars: int = 80,
        retry_min_digit_ratio: float = 0.05,
        retry_max_chars: int = 800,
    ) -> None:
        self.dpi = dpi
        self.psm = psm
        self.retry_psm = retry_psm
        self.retry_min_chars = retry_min_chars
        self.retry_min_digit_ratio = retry_min_digit_ratio
        self.retry_max_chars = retry_max_chars
        self._log = get_logger("edx.stages.text_extractor.ocr.tesseract")

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
        #
        # Process pages one at a time (first_page/last_page) so that peak
        # memory is bounded by one page's rendered image instead of all pages
        # simultaneously.  A 100-page PDF at 400 DPI loads ~4.6 GB when
        # rendered at once; one-at-a-time keeps it at ~46 MB per page.
        info = pdfinfo_from_path(str(pdf_path))
        total_pages = int(info.get("Pages", 0))
        lang_arg = "+".join(langs) if langs else "eng"

        pages: list[PageText] = []
        for page_num in range(1, total_pages + 1):
            images = convert_from_path(
                str(pdf_path), dpi=self.dpi,
                first_page=page_num, last_page=page_num,
            )
            if not images:
                continue
            image = images[0]
            text = self._run_once(image, lang_arg, self.psm)
            if self.retry_psm is not None and self._needs_retry(text):
                retry_text = self._run_once(image, lang_arg, self.retry_psm)
                if self._is_better(retry_text, text):
                    self._log.info(
                        "tesseract_retry_won",
                        page=page_num,
                        primary_psm=self.psm,
                        retry_psm=self.retry_psm,
                        primary_chars=len(text.strip()),
                        retry_chars=len(retry_text.strip()),
                    )
                    text = retry_text
            pages.append(PageText(page_number=page_num, text=text))
            del image  # free PIL image memory before next page
        return pages

    def _run_once(self, image: object, lang_arg: str, psm: int) -> str:
        config = f"--psm {psm}"
        return pytesseract.image_to_string(
            image, lang=lang_arg, config=config
        ) or ""

    def _needs_retry(self, text: str) -> bool:
        stripped = text.strip()
        if len(stripped) < self.retry_min_chars:
            return True
        # Long pages (narrative/table content >= retry_max_chars) have
        # substantial text already. PSM retry provides only ~1% improvement
        # on such pages at the cost of doubling OCR time — skip it.
        if len(stripped) >= self.retry_max_chars:
            return False
        digit_count = sum(1 for c in stripped if c.isdigit())
        return (
            digit_count / max(len(stripped), 1) < self.retry_min_digit_ratio
        )

    @staticmethod
    def _is_better(candidate: str, baseline: str) -> bool:
        return len(candidate.strip()) > len(baseline.strip())
