"""Thin wrapper around pymupdf for cheap inspection of PDF pages.

Patch 18: we classify *every* page as ``text`` or ``scan``, not the document
as a whole. The aggregate machine-readable flag is derived from the per-page
result downstream (≥1 text page → machine-readable). This lets banking RSBU
documents — where the auditor's narrative is text but the regulator forms
0409806/0409807 are scans — round-trip through the pipeline without silently
losing the scanned half.

Only the inspection helpers needed by the Classifier live here. Heavier
extraction is the Text Extractor's job (prompt 07).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

DEFAULT_MIN_TEXT_CHARS_PER_PAGE = 50

PageKind = Literal["text", "scan"]


@dataclass(frozen=True)
class PageClassification:
    """One row in the per-page classification list.

    ``page_index`` is 0-based to match Python iteration; the JSON we persist
    uses ``"page"`` to stay consistent with downstream PDF tooling that
    speaks 0-based indices internally too.
    """

    page_index: int
    char_count: int
    kind: PageKind


def count_pages(path: Path) -> int:
    """Return the total number of pages in ``path``."""
    with pymupdf.open(str(path)) as doc:  # type: ignore[no-untyped-call]
        return int(doc.page_count)


def extract_first_pages_text(
    path: Path,
    *,
    pages: int = 3,
) -> str:
    """Concatenate plain text from the first ``pages`` pages of the PDF.

    Used only for cheap heuristic detection of reporting standard / report
    form on the first inspected pages — not for the Text Extractor stage.
    """
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


def classify_pages(
    path: Path,
    *,
    min_text_chars_per_page: int = DEFAULT_MIN_TEXT_CHARS_PER_PAGE,
) -> list[PageClassification]:
    """Per-page classification of a PDF as machine-readable text vs. scan.

    A page is classified as ``"text"`` when ``pymupdf.get_text("text")``
    yields at least ``min_text_chars_per_page`` non-whitespace characters;
    anything below is a ``"scan"``. Threshold is configurable via
    ``app.classifier.min_text_chars_per_page``.

    Returns one :class:`PageClassification` per page in document order.
    Opens the PDF exactly once.
    """
    if min_text_chars_per_page < 1:
        raise ValueError("min_text_chars_per_page must be >= 1")
    out: list[PageClassification] = []
    with pymupdf.open(str(path)) as doc:  # type: ignore[no-untyped-call]
        for index in range(doc.page_count):
            page = doc.load_page(index)
            text = (page.get_text("text") or "").strip()
            char_count = len(text)
            kind: PageKind = (
                "text" if char_count >= min_text_chars_per_page else "scan"
            )
            out.append(
                PageClassification(
                    page_index=index, char_count=char_count, kind=kind
                )
            )
    return out
