"""``classify_pages`` against synthetic PDFs and the real fixtures.

Patch 18 — the multi-issuer principle is enforced here: per-page
classification is exercised on three real PDFs from three different
issuer/profile combinations.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from edx.stages.classifier.pdf_inspector import (
    PageClassification,
    classify_pages,
    count_pages,
    extract_first_pages_text,
)

REAL_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pdf"


def test_count_pages_text_pdf(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    pdf_path = make_text_pdf(tmp_path / "doc.pdf", "Hello world.")
    assert count_pages(pdf_path) == 1


def test_extract_first_pages_text_returns_visible_text(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    body = "IFRS consolidated financial statements for 2025.\n" * 30
    pdf_path = make_text_pdf(tmp_path / "report.pdf", body)
    text = extract_first_pages_text(pdf_path, pages=1)
    assert "IFRS" in text


# --- per-page classification: synthetic ---------------------------------


def test_classify_pages_text_pdf(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    body = "Lorem ipsum dolor sit amet.\n" * 20
    pdf_path = make_text_pdf(tmp_path / "doc.pdf", body)
    classes = classify_pages(pdf_path, min_text_chars_per_page=50)
    assert len(classes) == 1
    assert classes[0].kind == "text"
    assert classes[0].char_count >= 50
    assert isinstance(classes[0], PageClassification)


def test_classify_pages_scan_pdf(
    tmp_path: Path, make_scan_pdf: Callable[[Path], Path]
) -> None:
    pdf_path = make_scan_pdf(tmp_path / "scan.pdf")
    classes = classify_pages(pdf_path, min_text_chars_per_page=50)
    assert len(classes) == 1
    assert classes[0].kind == "scan"
    assert classes[0].char_count == 0


def test_classify_pages_threshold_boundary(
    tmp_path: Path, make_text_pdf: Callable[[Path, str], Path]
) -> None:
    """Page with exactly 3 chars: threshold 50 → scan, threshold 1 → text."""
    pdf_path = make_text_pdf(tmp_path / "tiny.pdf", "abc")
    assert classify_pages(pdf_path, min_text_chars_per_page=50)[0].kind == "scan"
    assert classify_pages(pdf_path, min_text_chars_per_page=1)[0].kind == "text"


# --- per-page classification: real fixtures (multi-issuer) --------------


def test_classify_pages_sber_rpbu_hybrid() -> None:
    """SBER RSBU 9M 2025: text-heavy intro then 13 scanned regulator forms."""
    classes = classify_pages(
        REAL_FIXTURES / "sber_rpbu_9m2025.pdf",
        min_text_chars_per_page=50,
    )
    text_indices = [c.page_index for c in classes if c.kind == "text"]
    scan_indices = [c.page_index for c in classes if c.kind == "scan"]
    assert text_indices == [0, 1, 2, 3]
    assert scan_indices == list(range(4, 17))


def test_classify_pages_lkoh_rsbu_all_text() -> None:
    """LKOH RSBU Q1 2026: pure text PDF — Patch 18 must not regress to OCR."""
    classes = classify_pages(
        REAL_FIXTURES / "lkoh_rsbu_q1_2026.pdf",
        min_text_chars_per_page=50,
    )
    assert len(classes) == 24
    assert all(c.kind == "text" for c in classes)
    assert sum(1 for c in classes if c.kind == "scan") == 0


def test_classify_pages_vtb_go_first30_all_text() -> None:
    """VTB Annual Report 2024 (first 30 pages): all native text."""
    classes = classify_pages(
        REAL_FIXTURES / "vtb_go_2024_first30.pdf",
        min_text_chars_per_page=50,
    )
    assert len(classes) == 30
    assert all(c.kind == "text" for c in classes)
