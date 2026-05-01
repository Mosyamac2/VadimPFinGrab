"""html_to_text on real-shaped fixtures."""

from __future__ import annotations

from pathlib import Path

from edx.stages.event_extractor.html_to_text import html_to_text

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "events"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_keeps_main_content_drops_chrome() -> None:
    text = html_to_text(_load("dividends_message.html"))
    # Body is preserved.
    assert "Решение о выплате (объявлении) дивидендов" in text
    assert "22,50 рубля" in text
    assert "12.06.2026" in text
    # Chrome is gone.
    assert "© 2026 e-disclosure.ru" not in text
    assert "Эмитенты" not in text  # nav link
    assert "Похожие сообщения" not in text  # aside
    assert "tracker" not in text  # script


def test_management_change_extracted_cleanly() -> None:
    text = html_to_text(_load("management_change.html"))
    assert "Иванов" in text
    assert "Совет директоров" in text
    assert "© ПАО Газпром, 2026" not in text
    assert "Главная" not in text


def test_empty_input_returns_empty() -> None:
    assert html_to_text("") == ""
    assert html_to_text("   \n  ") == ""


def test_collapses_blank_lines() -> None:
    text = html_to_text("<html><body><p>A</p><p></p><p></p><p>B</p></body></html>")
    # No more than one blank line between paragraphs.
    assert "\n\n\n" not in text
