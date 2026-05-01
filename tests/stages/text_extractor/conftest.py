"""Shared fixtures for text_extractor tests: synthetic PDFs/PNGs."""

from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
import pytest


def _make_pdf(target: Path, *, body: str | None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if body is not None:
        y = 50.0
        for line in body.splitlines() or [""]:
            page.insert_text(
                pymupdf.Point(50, y), line, fontsize=11, fontname="helv"
            )
            y += 14
    else:
        page.draw_rect(
            pymupdf.Rect(0, 0, 595, 842), color=(1, 1, 1), fill=(1, 1, 1)
        )
    doc.save(str(target))
    doc.close()
    return target


@pytest.fixture
def make_text_pdf() -> object:
    def _factory(target: Path, body: str) -> Path:
        return _make_pdf(target, body=body)

    return _factory


@pytest.fixture
def make_scan_pdf() -> object:
    def _factory(target: Path) -> Path:
        return _make_pdf(target, body=None)

    return _factory


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


requires_tesseract = pytest.mark.skipif(
    not tesseract_available(), reason="tesseract binary not on PATH"
)
