"""native.extract_text / extract_tables on synthetic PDFs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from edx.stages.text_extractor.native import extract_tables, extract_text


def test_extract_text_round_trips_known_string(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    body = "Hello extracted world.\nIFRS consolidated 2025."
    pdf = make_text_pdf(tmp_path / "doc.pdf", body)
    pages = extract_text(pdf)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    text = pages[0].text
    assert "Hello extracted world." in text
    assert "IFRS consolidated 2025." in text


def test_extract_tables_returns_empty_for_text_only_pdf(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    pdf = make_text_pdf(tmp_path / "doc.pdf", "Just some text, no tables here.")
    tables = extract_tables(pdf)
    assert len(tables) == 1
    page_no, table_list = tables[0]
    assert page_no == 1
    # pdfplumber may return [] for text-only pages.
    assert table_list == []
