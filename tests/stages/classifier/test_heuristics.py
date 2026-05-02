"""Pure-function heuristic tests."""

from __future__ import annotations

from edx.stages.classifier.heuristics import (
    detect_report_form,
    detect_reporting_standard,
)


def test_detect_reporting_standard_ifrs() -> None:
    text = "Group Sberbank consolidated financial statements (IFRS) — МСФО"
    assert detect_reporting_standard(text) == "IFRS"


def test_detect_reporting_standard_rsbu() -> None:
    text = (
        "Бухгалтерский баланс. Форма по ОКУД 0710001. "
        "Приказ Минфина России. ПБУ 4/99."
    )
    assert detect_reporting_standard(text) == "RSBU"


def test_detect_reporting_standard_other_for_irrelevant_text() -> None:
    assert (
        detect_reporting_standard("Это просто пресс-релиз без терминов.")
        == "OTHER"
    )


def test_detect_reporting_standard_other_on_empty() -> None:
    assert detect_reporting_standard("") == "OTHER"


def test_detect_reporting_standard_other_on_tie() -> None:
    text = "ПБУ 4/99 + IFRS"  # one RSBU marker (пбу) and one IFRS (ifrs).
    assert detect_reporting_standard(text) == "OTHER"


def test_detect_reporting_standard_returns_dominant() -> None:
    text = "ПБУ 4/99 один раз. IFRS дважды: IFRS и IFRS."
    assert detect_reporting_standard(text) == "IFRS"


def test_detect_report_form_balance_sheet() -> None:
    assert detect_report_form("Бухгалтерский баланс на 31 декабря") == "balance_sheet"
    assert (
        detect_report_form("Statement of Financial Position as at 31 Dec")
        == "balance_sheet"
    )


def test_detect_report_form_income_statement() -> None:
    assert (
        detect_report_form("Отчёт о финансовых результатах за 2025 год")
        == "income_statement"
    )


def test_detect_report_form_cash_flow() -> None:
    assert (
        detect_report_form("Отчёт о движении денежных средств") == "cash_flow"
    )


def test_detect_report_form_notes() -> None:
    assert (
        detect_report_form(
            "Notes to the consolidated financial statements: significant accounting policies"
        )
        == "notes"
    )


def test_detect_report_form_cover() -> None:
    assert detect_report_form("Аудиторское заключение независимый аудит") == "cover"


def test_detect_report_form_other_default() -> None:
    assert detect_report_form("Просто текст без признаков формы.") == "other"
    assert detect_report_form("") == "other"
