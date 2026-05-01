"""Native (non-OCR) extraction: pymupdf for text, pdfplumber for tables."""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pymupdf

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
