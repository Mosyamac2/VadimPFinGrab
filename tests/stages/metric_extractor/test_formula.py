"""Safe formula evaluator."""

from __future__ import annotations

import pytest

from edx.stages.metric_extractor.formula import safe_formula_eval


def test_simple_addition() -> None:
    assert safe_formula_eval(
        "a + b", {"a": 10, "b": 5}
    ) == 15


def test_full_ebitda_formula() -> None:
    values = {
        "net_income": 100.0,
        "depreciation_amortization": 50.0,
        "interest_expense": 10.0,
        "tax_expense": 25.0,
    }
    result = safe_formula_eval(
        "net_income + depreciation_amortization + interest_expense + tax_expense",
        values,
    )
    assert result == 185.0


def test_missing_operand_returns_none() -> None:
    assert safe_formula_eval(
        "a + b", {"a": 10}
    ) is None
    assert safe_formula_eval(
        "a + b", {"a": 10, "b": None}
    ) is None


def test_division_by_zero_returns_none() -> None:
    assert safe_formula_eval("a / b", {"a": 10, "b": 0}) is None


def test_unsupported_node_rejected() -> None:
    with pytest.raises(ValueError):
        safe_formula_eval("os.system('rm -rf /')", {})


def test_invalid_syntax_rejected() -> None:
    with pytest.raises(ValueError):
        safe_formula_eval("a +", {"a": 1})
