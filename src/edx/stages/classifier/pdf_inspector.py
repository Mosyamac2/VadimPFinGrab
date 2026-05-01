"""Thin wrapper around pymupdf for cheap inspection of PDF pages.

Only the functions needed by the Classifier are here — heavier extraction is
the Text Extractor's job (prompt 07).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

DEFAULT_MIN_TEXT_CHARS = 400
DEFAULT_FIRST_PAGES = 3


def count_pages(path: Path) -> int:
    """Return the total number of pages in ``path``."""
    with pymupdf.open(str(path)) as doc:  # type: ignore[no-untyped-call]
        return int(doc.page_count)


def extract_first_pages_text(
    path: Path,
    *,
    pages: int = DEFAULT_FIRST_PAGES,
) -> str:
    """Concatenate plain text from the first ``pages`` pages of the PDF."""
    if pages < 1:
        raise ValueError("pages must be >= 1")
    parts: list[str] = []
    with pymupdf.open(str(path)) as doc:  # type: ignore[no-untyped-call]
        last = min(pages, doc.page_count)
        for i in range(last):
            page = doc.load_page(i)
            text = page.get_text("text")
            if text:
                parts.append(text)
    return "\n".join(parts)


def is_machine_readable(
    path: Path,
    *,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
    pages: int = DEFAULT_FIRST_PAGES,
) -> bool:
    """``True`` when the first ``pages`` pages contain a non-trivial text layer.

    Threshold (``min_text_chars``) controls how aggressive we are about routing
    a PDF to OCR — see ``app.classifier.min_text_chars``.
    """
    text = extract_first_pages_text(path, pages=pages)
    return len(text.strip()) >= min_text_chars
