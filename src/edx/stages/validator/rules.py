"""Pure sanity-check rules (ТЗ §11.1).

Each rule operates on plain :class:`MetricRow` lists or scalars. No I/O, no
logging, no DB. Service code groups rows and threads previous-period data;
rules just compute warnings.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from edx.storage import MetricRow

# Canonical metric names referenced by the rules. Operators may extend
# ``metrics.yaml`` without breaking these — checks are skipped when a metric
# is absent.
METRIC_TOTAL_ASSETS: Final[str] = "total_assets"
METRIC_TOTAL_EQUITY: Final[str] = "total_equity"
METRIC_TOTAL_LIABILITIES: Final[str] = "total_liabilities"
METRIC_REVENUE: Final[str] = "revenue"
METRIC_NET_INCOME: Final[str] = "net_income"
METRIC_EBITDA: Final[str] = "ebitda"

BALANCE_TOLERANCE_RATIO: Final[float] = 0.005  # ±0.5% per ТЗ §11.1
YOY_RATIO_THRESHOLD: Final[float] = 10.0  # > 10x flagged as suspicious


class QAWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    affected_metrics: list[str] = Field(default_factory=list)


def _values_by_name(metrics: Iterable[MetricRow]) -> dict[str, float]:
    """Collect ``metric_name → value`` only for non-null values."""
    return {m.metric_name: float(m.value) for m in metrics if m.value is not None}


def check_balance_equation(metrics: list[MetricRow]) -> list[QAWarning]:
    """Активы ≈ Капитал + Обязательства, ±0.5%.

    Skipped silently when any of the three is missing.
    """
    values = _values_by_name(metrics)
    if not all(
        name in values
        for name in (
            METRIC_TOTAL_ASSETS,
            METRIC_TOTAL_EQUITY,
            METRIC_TOTAL_LIABILITIES,
        )
    ):
        return []
    assets = values[METRIC_TOTAL_ASSETS]
    equity = values[METRIC_TOTAL_EQUITY]
    liabilities = values[METRIC_TOTAL_LIABILITIES]
    diff = abs(assets - (equity + liabilities))
    tolerance = abs(assets) * BALANCE_TOLERANCE_RATIO
    if diff > tolerance:
        return [
            QAWarning(
                code="balance_mismatch",
                message=(
                    f"Активы={assets:.2f} ≠ Капитал+Обязательства="
                    f"{equity + liabilities:.2f} (отклонение {diff:.2f}, "
                    f"допуск {tolerance:.2f})"
                ),
                affected_metrics=[
                    METRIC_TOTAL_ASSETS,
                    METRIC_TOTAL_EQUITY,
                    METRIC_TOTAL_LIABILITIES,
                ],
            )
        ]
    return []


def check_signs(metrics: list[MetricRow]) -> list[QAWarning]:
    """Активы и Выручка — неотрицательные. EBITDA / чистая прибыль не проверяются."""
    warnings: list[QAWarning] = []
    values = _values_by_name(metrics)
    if METRIC_TOTAL_ASSETS in values and values[METRIC_TOTAL_ASSETS] < 0:
        warnings.append(
            QAWarning(
                code="negative_assets",
                message=(
                    f"Total assets отрицательны: {values[METRIC_TOTAL_ASSETS]:.2f}"
                ),
                affected_metrics=[METRIC_TOTAL_ASSETS],
            )
        )
    if METRIC_REVENUE in values and values[METRIC_REVENUE] < 0:
        warnings.append(
            QAWarning(
                code="negative_revenue",
                message=f"Revenue отрицательна: {values[METRIC_REVENUE]:.2f}",
                affected_metrics=[METRIC_REVENUE],
            )
        )
    return warnings


def check_yoy(
    current: list[MetricRow],
    previous: list[MetricRow] | None,
) -> list[QAWarning]:
    """Flag year-over-year changes that exceed 10x in absolute terms.

    Both the current and previous values must be non-null and non-zero for
    the rule to compare; otherwise the metric pair is skipped silently.
    """
    if not previous:
        return []
    cur = _values_by_name(current)
    prev = _values_by_name(previous)
    warnings: list[QAWarning] = []
    for name, cur_val in cur.items():
        if name not in prev:
            continue
        prev_val = prev[name]
        if cur_val == 0 or prev_val == 0:
            continue
        ratio = max(abs(cur_val), abs(prev_val)) / min(
            abs(cur_val), abs(prev_val)
        )
        if ratio > YOY_RATIO_THRESHOLD:
            warnings.append(
                QAWarning(
                    code="suspicious_yoy",
                    message=(
                        f"{name}: изменение в {ratio:.1f} раза "
                        f"(prev={prev_val:.2f}, cur={cur_val:.2f})"
                    ),
                    affected_metrics=[name],
                )
            )
    return warnings


def check_currency_consistency(
    metrics: list[MetricRow],
) -> list[QAWarning]:
    """Все строки одной публикации — в одной валюте."""
    currencies = {m.currency for m in metrics if m.currency}
    if len(currencies) <= 1:
        return []
    return [
        QAWarning(
            code="currency_mixed",
            message=f"В публикации смешаны валюты: {sorted(currencies)}",
            affected_metrics=[],
        )
    ]


def check_unit_consistency(metrics: list[MetricRow]) -> list[QAWarning]:
    """Все строки одной публикации — в одной единице измерения."""
    units = {m.unit for m in metrics if m.unit}
    if len(units) <= 1:
        return []
    return [
        QAWarning(
            code="unit_mixed",
            message=f"В публикации смешаны единицы: {sorted(units)}",
            affected_metrics=[],
        )
    ]


def check_completeness(
    extracted_count: int,
    requested_count: int,
    threshold: float,
) -> list[QAWarning]:
    """Coverage < threshold → publication is incomplete (ТЗ §11.2)."""
    if requested_count <= 0:
        return []
    ratio = extracted_count / requested_count
    if ratio >= threshold:
        return []
    return [
        QAWarning(
            code="incomplete",
            message=(
                f"Извлечено {extracted_count} из {requested_count} "
                f"показателей ({ratio:.0%} < порога {threshold:.0%})"
            ),
            affected_metrics=[],
        )
    ]
