"""Native (non-OCR) extraction: pymupdf for text, pdfplumber for tables.

Patch 36 adds RTF support — some Russian issuers (PHOR, SLGD, …) ship
their Issuer Reports in `.rtf` instead of PDF. ``extract_text_from_rtf``
returns a single synthetic page (RTF doesn't carry reliable page
boundaries; downstream stages join pages anyway).
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pymupdf
from striprtf.striprtf import rtf_to_text

from edx.stages.text_extractor.models import PageText, Table


def extract_text(pdf_path: Path) -> list[PageText]:
    """Per-page text via pymupdf. No table data attached here."""
    pages: list[PageText] = []
    with pymupdf.open(str(pdf_path)) as doc:  # type: ignore[no-untyped-call]
        for index in range(doc.page_count):
            page = doc.load_page(index)
            text = page.get_text("text") or ""
            pages.append(PageText(page_number=index + 1, text=text))
    return pages


def extract_text_from_rtf(rtf_path: Path) -> list[PageText]:
    """Read a .rtf file, strip RTF control words, return single-page text.

    RTF doesn't carry reliable page boundaries (Word-RTF emits
    ``\\page`` only when the user inserts a manual break), so we treat
    the whole document as one synthetic page numbered 1. The Metric
    Extractor concatenates pages anyway — having one ``--- page 1 ---``
    marker in the assembled user_text doesn't hurt.

    Falls back gracefully on broken / empty RTF: returns a single
    PageText with empty text. Encoding is read with ``errors='replace'``
    because Russian RTF в дикой природе может быть как UTF-8 (новые
    Word), так и CP1251 (старый OpenOffice) — striprtf нормализует.
    """
    raw = rtf_path.read_text(encoding="utf-8", errors="replace")
    plain: str = rtf_to_text(raw, errors="ignore") or ""  # type: ignore[no-untyped-call]
    return [PageText(page_number=1, text=plain)]


def extract_tables(pdf_path: Path) -> list[tuple[int, list[Table]]]:
    """Per-page tables via pdfplumber. Returns ``(page_number, tables)`` pairs.

    pdfplumber returns tables as ``list[list[list[str | None]]]`` already.
    """
    out: list[tuple[int, list[Table]]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages):
            try:
                tables = page.extract_tables() or []
            except Exception:  # noqa: BLE001 — pdfplumber occasionally chokes
                tables = []
            out.append((index + 1, tables))
    return out
