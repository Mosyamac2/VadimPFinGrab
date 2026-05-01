"""pdf_inspector wrappers — tested against synthesised PDFs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from edx.stages.classifier.pdf_inspector import (
    count_pages,
    extract_first_pages_text,
    is_machine_readable,
)


def test_count_pages_text_pdf(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    pdf_path = make_text_pdf(tmp_path / "doc.pdf", "Hello world.")
    assert count_pages(pdf_path) == 1


def test_text_pdf_is_machine_readable(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    # Helvetica supports Latin characters reliably; the heuristic markers
    # we care about (IFRS / consolidated / Group) are all Latin so this is
    # representative for the test purpose.
    body = "IFRS consolidated financial statements for 2025.\n" * 30
    pdf_path = make_text_pdf(tmp_path / "report.pdf", body)
    assert is_machine_readable(pdf_path, min_text_chars=200, pages=1) is True
    text = extract_first_pages_text(pdf_path, pages=1)
    assert "IFRS" in text


def test_scan_pdf_is_not_machine_readable(
    tmp_path: Path, make_scan_pdf: Callable[[Path], Path]
) -> None:
    pdf_path = make_scan_pdf(tmp_path / "scan.pdf")
    assert pdf_path.stat().st_size <= 100 * 1024
    assert is_machine_readable(pdf_path, min_text_chars=200, pages=1) is False


def test_short_text_pdf_below_threshold(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    pdf_path = make_text_pdf(tmp_path / "tiny.pdf", "abc")
    # Threshold = 50: 3 chars of "abc" is plainly below.
    assert is_machine_readable(pdf_path, min_text_chars=50, pages=1) is False
