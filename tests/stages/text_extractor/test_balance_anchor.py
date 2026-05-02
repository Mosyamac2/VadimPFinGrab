"""Patch 30: balance-sheet anchor trim for RSBU documents."""

from __future__ import annotations

from edx.stages.text_extractor.balance_anchor import (
    extract_balance_sheet_onwards,
)

# Reusable preamble — emulates a multi-page audit opinion (Кэпт style)
# without binding the test to a specific issuer's wording.
_PREAMBLE = (
    "Аудиторское заключение независимых аудиторов\n"
    "о бухгалтерской отчётности ПАО «Рога и Копыта» за 2025 год.\n\n"
    "Мы провели аудит бухгалтерской отчётности Компании, состоящей из "
    "бухгалтерского баланса по состоянию на 31 декабря 2025 года, отчёта "
    "о финансовых результатах за 2025 год, отчёта об изменениях капитала "
    "за 2025 год и отчёта о движении денежных средств за 2025 год.\n\n"
    "Наше мнение: прилагаемая бухгалтерская отчётность отражает достоверно "
    "во всех существенных аспектах финансовое положение Компании.\n"
    "Ключевые вопросы аудита: оценка на обесценение основных средств, "
    "тестирование стоимости запасов.\n\n"
)

_FORM_BODY = (
    "Код 0710001\n"
    "Актив\n"
    "I. Внеоборотные активы\n"
    "Основные средства 1150  397 216 398\n"
    "II. Оборотные активы\n"
    "Запасы 1210 73 327 449\n"
    "БАЛАНС 1700 846 546 320\n"
)


def test_anchor_uppercase_balance_label_found() -> None:
    text = _PREAMBLE + "БУХГАЛТЕРСКИЙ БАЛАНС\n" + _FORM_BODY
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.anchor_label_seen == "БУХГАЛТЕРСКИЙ БАЛАНС"
    assert result.warnings == ()
    assert result.content.startswith("Перед тобой формы РСБУ-отчётности")
    assert "БУХГАЛТЕРСКИЙ БАЛАНС" in result.content
    assert "БАЛАНС 1700 846 546 320" in result.content
    # Audit opinion text should be gone.
    assert "Аудиторское заключение" not in result.content
    assert "Ключевые вопросы аудита" not in result.content


def test_anchor_capitalised_label_found() -> None:
    text = _PREAMBLE + "Бухгалтерский баланс\n" + _FORM_BODY
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.anchor_label_seen == "Бухгалтерский баланс"
    assert "Бухгалтерский баланс" in result.content


def test_anchor_okud_form_code_found() -> None:
    """OKUD code suffices even without a formal heading line."""
    text = _PREAMBLE + "Форма по ОКУД 0710001\n" + _FORM_BODY
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.anchor_label_seen == "ОКУД 0710001"
    assert "0710001" in result.content


def test_anchor_okud_form_code_with_spaces_and_no_leading_zero() -> None:
    text = _PREAMBLE + "Форма  по\tОКУД\n710001\n" + _FORM_BODY
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.anchor_label_seen == "ОКУД 0710001"


def test_falls_back_to_pl_anchor() -> None:
    """When balance heading is absent but P&L is present — take P&L."""
    text = (
        _PREAMBLE
        + "ОТЧЕТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ\n"
        + "Выручка 2110 620 099 738\n"
    )
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.anchor_label_seen == "ОТЧЕТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ"
    assert "Выручка 2110" in result.content


def test_no_anchor_returns_none() -> None:
    """Pure audit text with no form anchor → fall-soft to None."""
    text = _PREAMBLE + "Конец заключения. Подпись.\n"
    result = extract_balance_sheet_onwards(text)
    assert result.content is None
    assert result.anchor_label_seen is None
    assert result.warnings == ("balance_anchor_not_found",)


def test_max_chars_truncates_with_warning() -> None:
    long_body = "x" * 300_000
    text = "БУХГАЛТЕРСКИЙ БАЛАНС\n" + long_body
    result = extract_balance_sheet_onwards(text, max_chars=100_000)
    assert result.content is not None
    # Content = lead header + first 100k chars of body.
    assert len(result.content) <= 100_000 + 500  # header is short
    assert "balance_trim_capped" in result.warnings


def test_earliest_anchor_wins_among_competing_matches() -> None:
    """If both balance and P&L appear, the earliest in text wins."""
    text = (
        _PREAMBLE
        + "ОТЧЕТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ\n"  # appears first
        + "Выручка 2110 620 099 738\n\n"
        + "БУХГАЛТЕРСКИЙ БАЛАНС\n"  # appears second
        + _FORM_BODY
    )
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.anchor_label_seen == "ОТЧЕТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ"
    # Both blocks present in the trimmed slice.
    assert "Выручка 2110" in result.content
    assert "БУХГАЛТЕРСКИЙ БАЛАНС" in result.content


def test_prefix_header_included() -> None:
    text = "БУХГАЛТЕРСКИЙ БАЛАНС\n" + _FORM_BODY
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.content.startswith("Перед тобой формы РСБУ-отчётности")


def test_empty_input_returns_none() -> None:
    result = extract_balance_sheet_onwards("")
    assert result.content is None
    assert result.warnings == ("balance_anchor_not_found",)


def test_inline_balance_label_does_not_match() -> None:
    """The uppercase anchor must be on its own line; substring inside
    a paragraph (e.g. quoting another doc) shouldn't trigger.
    """
    text = (
        "Подробности см. в разделе БУХГАЛТЕРСКИЙ БАЛАНС "
        "прошлогоднего отчёта.\nКонец.\n"
    )
    result = extract_balance_sheet_onwards(text)
    # On-its-own-line constraint via (?im)^\s*...\s*$ — inline mention
    # at the start of a sentence shouldn't hit. But a multi-anchor
    # search might still match via a fall-back anchor — we just want
    # to confirm we don't pick up the inline reference as a heading.
    if result.content is not None:
        # If something matched at all, it must not be the inline ref.
        assert result.anchor_label_seen != "БУХГАЛТЕРСКИЙ БАЛАНС"
    else:
        assert result.warnings == ("balance_anchor_not_found",)


def test_okud_code_inside_running_text_still_matches() -> None:
    """The OKUD anchor uses \\b word-boundary, not line anchors —
    references like "форма по ОКУД 0710001" appear naturally inside
    the form body and must still trigger.
    """
    text = (
        _PREAMBLE
        + "Бухгалтерская отчётность Форма по ОКУД 0710001 за 2025 год\n"
        + _FORM_BODY
    )
    result = extract_balance_sheet_onwards(text)
    assert result.content is not None
    assert result.anchor_label_seen == "ОКУД 0710001"
