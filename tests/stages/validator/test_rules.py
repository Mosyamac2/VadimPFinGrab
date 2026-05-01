"""Pure rule tests (no DB, no LLM)."""

from __future__ import annotations

from edx.stages.validator.rules import (
    check_balance_equation,
    check_completeness,
    check_currency_consistency,
    check_signs,
    check_unit_consistency,
    check_yoy,
)
from edx.storage import MetricRow


def _row(
    name: str,
    value: float | None,
    *,
    currency: str = "RUB",
    unit: str = "ones",
    metric_id: int = 1,
    reporting_date: str = "2025-12-31",
    period_type: str = "FY",
    reporting_standard: str = "IFRS",
    ticker: str = "SBER",
) -> MetricRow:
    return MetricRow(
        metric_id=metric_id,
        ticker=ticker,
        reporting_date=reporting_date,
        period_type=period_type,  # type: ignore[arg-type]
        reporting_standard=reporting_standard,  # type: ignore[arg-type]
        metric_name=name,
        value=value,
        currency=currency,
        unit=unit,
        source_document_id=1,
        qa_warning=None,
        extracted_at="2026-05-01T00:00:00+00:00",
    )


# ----- balance equation -----


def test_balance_equation_within_tolerance_no_warning() -> None:
    metrics = [
        _row("total_assets", 1000.0),
        _row("total_equity", 600.0),
        _row("total_liabilities", 400.0),
    ]
    assert check_balance_equation(metrics) == []


def test_balance_equation_exceeds_half_percent_emits_warning() -> None:
    metrics = [
        _row("total_assets", 1000.0),
        _row("total_equity", 600.0),
        _row("total_liabilities", 350.0),
    ]
    warnings = check_balance_equation(metrics)
    assert len(warnings) == 1
    assert warnings[0].code == "balance_mismatch"
    assert set(warnings[0].affected_metrics) == {
        "total_assets", "total_equity", "total_liabilities",
    }


def test_balance_equation_skipped_when_inputs_missing() -> None:
    metrics = [
        _row("total_assets", 1000.0),
        _row("total_equity", 600.0),
    ]
    assert check_balance_equation(metrics) == []


# ----- signs -----


def test_negative_revenue_warning() -> None:
    metrics = [_row("revenue", -100.0)]
    warnings = check_signs(metrics)
    assert len(warnings) == 1
    assert warnings[0].code == "negative_revenue"


def test_negative_assets_warning() -> None:
    metrics = [_row("total_assets", -50.0)]
    warnings = check_signs(metrics)
    assert len(warnings) == 1
    assert warnings[0].code == "negative_assets"


def test_negative_net_income_or_ebitda_no_warning() -> None:
    metrics = [
        _row("net_income", -200.0),
        _row("ebitda", -50.0),
        _row("revenue", 1000.0),
        _row("total_assets", 5000.0),
    ]
    assert check_signs(metrics) == []


# ----- YoY -----


def test_yoy_11x_growth_emits_warning() -> None:
    cur = [_row("revenue", 1100.0)]
    prev = [_row("revenue", 100.0)]
    warnings = check_yoy(cur, prev)
    assert len(warnings) == 1
    assert warnings[0].code == "suspicious_yoy"
    assert warnings[0].affected_metrics == ["revenue"]


def test_yoy_5x_growth_no_warning() -> None:
    cur = [_row("revenue", 500.0)]
    prev = [_row("revenue", 100.0)]
    assert check_yoy(cur, prev) == []


def test_yoy_collapse_to_zero_skipped_silently() -> None:
    cur = [_row("revenue", 0.0)]
    prev = [_row("revenue", 100.0)]
    assert check_yoy(cur, prev) == []


def test_yoy_no_previous_returns_empty() -> None:
    cur = [_row("revenue", 1000.0)]
    assert check_yoy(cur, None) == []
    assert check_yoy(cur, []) == []


# ----- consistency -----


def test_currency_mixed_warning() -> None:
    metrics = [
        _row("revenue", 1.0, currency="RUB"),
        _row("net_income", 0.5, currency="USD"),
    ]
    warnings = check_currency_consistency(metrics)
    assert len(warnings) == 1
    assert warnings[0].code == "currency_mixed"


def test_unit_mixed_warning() -> None:
    metrics = [
        _row("revenue", 1.0, unit="ones"),
        _row("net_income", 0.5, unit="thousands"),
    ]
    warnings = check_unit_consistency(metrics)
    assert len(warnings) == 1
    assert warnings[0].code == "unit_mixed"


def test_consistency_single_value_no_warning() -> None:
    metrics = [
        _row("revenue", 1.0, currency="RUB", unit="ones"),
        _row("net_income", 0.5, currency="RUB", unit="ones"),
    ]
    assert check_currency_consistency(metrics) == []
    assert check_unit_consistency(metrics) == []


# ----- completeness -----


def test_completeness_below_threshold() -> None:
    warnings = check_completeness(
        extracted_count=2, requested_count=5, threshold=0.5
    )
    assert len(warnings) == 1
    assert warnings[0].code == "incomplete"


def test_completeness_at_or_above_threshold_no_warning() -> None:
    assert (
        check_completeness(extracted_count=3, requested_count=5, threshold=0.5)
        == []
    )
    assert (
        check_completeness(extracted_count=5, requested_count=5, threshold=1.0)
        == []
    )


def test_completeness_zero_requested_skipped() -> None:
    assert (
        check_completeness(extracted_count=0, requested_count=0, threshold=0.5)
        == []
    )
