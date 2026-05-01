"""Pure-function text cleanup."""

from __future__ import annotations

from edx.stages.text_extractor.normalize import normalize_text


def test_soft_hyphens_removed() -> None:
    raw = "перенос­соединён"
    cleaned = normalize_text([raw])
    assert cleaned == ["переноссоединён"]


def test_hyphenated_linebreak_joined() -> None:
    raw = "финан-\nсовая отчётность"
    cleaned = normalize_text([raw])
    assert cleaned == ["финансовая отчётность"]


def test_collapse_runs_of_spaces_and_blank_lines() -> None:
    raw = "Слово   слово\n\n\n\nещё абзац  "
    cleaned = normalize_text([raw])
    assert cleaned == ["Слово слово\n\nещё абзац"]


def test_recurring_header_footer_stripped() -> None:
    pages = [
        "ПАО Сбербанк — годовой отчёт\nКонтент первой страницы\n© 2026",
        "ПАО Сбербанк — годовой отчёт\nКонтент второй страницы\n© 2026",
        "ПАО Сбербанк — годовой отчёт\nКонтент третьей страницы\n© 2026",
    ]
    cleaned = normalize_text(pages, header_footer_min_pages=3)
    for page in cleaned:
        assert "годовой отчёт" not in page
        assert "© 2026" not in page
    assert "Контент первой страницы" in cleaned[0]
    assert "Контент второй страницы" in cleaned[1]


def test_short_doc_does_not_strip_anything() -> None:
    pages = [
        "Header X\nbody A",
        "Header X\nbody B",
    ]
    cleaned = normalize_text(pages, header_footer_min_pages=3)
    # Only 2 pages — below threshold, header stays.
    assert all("Header X" in p for p in cleaned)


def test_empty_pages_pass_through() -> None:
    assert normalize_text([""]) == [""]
    assert normalize_text(["", "", ""]) == ["", "", ""]
