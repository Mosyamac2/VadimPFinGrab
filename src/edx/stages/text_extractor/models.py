"""Value objects shared by native and OCR text extraction code paths."""

from __future__ import annotations

from dataclasses import dataclass

# A table is rows of cells; cells are strings (or None where empty).
Table = list[list[str | None]]


@dataclass(frozen=True)
class PageText:
    """One page of extracted text + (optionally) its tables."""

    page_number: int
    text: str
    tables: list[Table] | None = None
