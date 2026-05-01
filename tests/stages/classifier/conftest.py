"""Helpers for synthesising tiny PDFs used by classifier tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest


def _write_pdf(target: Path, text: str | None) -> Path:
    """Synthesize a small PDF. ``text=None`` produces a scan-like (no text) page."""
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if text is not None:
        # Word-wrap manually for long blocks so each line fits the page width.
        lines = text.splitlines() or [""]
        y = 50.0
        for line in lines:
            page.insert_text(
                pymupdf.Point(50, y),
                line,
                fontsize=11,
                fontname="helv",
            )
            y += 14
    else:
        # No text layer at all → counts as a scan.
        page.draw_rect(
            pymupdf.Rect(0, 0, 595, 842), color=(1, 1, 1), fill=(1, 1, 1)
        )
    doc.save(str(target))
    doc.close()
    return target


@pytest.fixture
def make_text_pdf() -> object:
    def _factory(target: Path, text: str) -> Path:
        return _write_pdf(target, text=text)

    return _factory


@pytest.fixture
def make_scan_pdf() -> object:
    def _factory(target: Path) -> Path:
        return _write_pdf(target, text=None)

    return _factory


SAMPLE_IFRS_TEXT = """\
Группа Сбербанк
Консолидированная финансовая отчётность
по Международными стандартами финансовой отчётности (МСФО / IFRS)
за год, закончившийся 31 декабря 2025 года.
Statement of Financial Position
Consolidated income statement
""" * 12

SAMPLE_RSBU_TEXT = """\
Бухгалтерский баланс
Форма по ОКУД 0710001
Утверждена приказом Минфина России от 02.07.2010
ПБУ 4/99 положение по бухгалтерскому учёту
ОАО Сбербанк России — РСБУ за 2025 год
""" * 12
