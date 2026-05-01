"""ValidatorService — applies sanity checks and writes qa_issues."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from edx.logging_setup import get_logger
from edx.stages.validator.rules import (
    QAWarning,
    check_balance_equation,
    check_completeness,
    check_currency_consistency,
    check_signs,
    check_unit_consistency,
    check_yoy,
)
from edx.storage import (
    MetricRow,
    MetricsRepo,
    PublicationRow,
    PublicationsRepo,
    QAIssuesRepo,
)


@dataclass(frozen=True)
class ValidatorOutcome:
    publication_id: str
    warnings_count: int
    metric_rows_flagged: int
    is_incomplete: bool


PeriodKey = tuple[str, str, str]  # (reporting_date, period_type, reporting_standard)


class ValidatorService:
    def __init__(
        self,
        publications_repo: PublicationsRepo,
        metrics_repo: MetricsRepo,
        qa_issues_repo: QAIssuesRepo,
        *,
        completeness_threshold: float = 0.5,
        metrics_per_period: int = 0,
    ) -> None:
        self.publications_repo = publications_repo
        self.metrics_repo = metrics_repo
        self.qa_issues_repo = qa_issues_repo
        self.completeness_threshold = completeness_threshold
        self.metrics_per_period = metrics_per_period
        self._log = get_logger("edx.stages.validator")

    def run(
        self, publications: Iterable[PublicationRow]
    ) -> list[ValidatorOutcome]:
        outcomes: list[ValidatorOutcome] = []
        for pub in publications:
            try:
                outcome = self._validate_one(pub)
            except Exception as exc:  # noqa: BLE001 — fail-soft per ТЗ §14
                self._log.error(
                    "validator_failed",
                    publication_id=pub.publication_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                continue
            outcomes.append(outcome)
        return outcomes

    def _validate_one(self, pub: PublicationRow) -> ValidatorOutcome:
        metrics = self.metrics_repo.list_for_publication(pub.publication_id)
        warnings: list[QAWarning] = []

        # Period-level checks.
        periods = _group_by_period(metrics)
        for key, period_metrics in periods.items():
            warnings.extend(check_balance_equation(period_metrics))
            warnings.extend(check_signs(period_metrics))
            previous_metrics = self._previous_period_metrics(pub.ticker, key)
            warnings.extend(check_yoy(period_metrics, previous_metrics))

        # Publication-level checks.
        warnings.extend(check_currency_consistency(metrics))
        warnings.extend(check_unit_consistency(metrics))

        # Completeness over expected (metrics_per_period × periods) vs extracted.
        if periods:
            expected_per_period = (
                self.metrics_per_period
                if self.metrics_per_period
                else len(periods[next(iter(periods))])
            )
            requested = expected_per_period * len(periods)
        else:
            requested = self.metrics_per_period
        extracted = sum(1 for m in metrics if m.value is not None)
        completeness_warnings = check_completeness(
            extracted, requested, self.completeness_threshold
        )
        warnings.extend(completeness_warnings)

        # Persist warnings on each metric row.
        flagged = self._attach_qa_warnings(metrics, warnings)

        # Persist publication-level qa_issues (idempotent replace).
        issues_payload = [(w.code, w.message) for w in warnings]
        self.qa_issues_repo.replace_for_publication(
            pub.publication_id, pub.ticker, issues_payload
        )

        # Mirror incompleteness onto the publication row so downstream stages
        # (Writer, Excel mart) can filter without re-computing.
        is_incomplete = any(w.code == "incomplete" for w in warnings)
        self.publications_repo.mark_incomplete(
            pub.publication_id, is_incomplete
        )
        self.publications_repo.mark_status(pub.publication_id, "validated")

        self._log.info(
            "publication_validated",
            publication_id=pub.publication_id,
            warnings=len(warnings),
            metric_rows_flagged=flagged,
            is_incomplete=is_incomplete,
            codes=sorted({w.code for w in warnings}),
        )
        return ValidatorOutcome(
            publication_id=pub.publication_id,
            warnings_count=len(warnings),
            metric_rows_flagged=flagged,
            is_incomplete=is_incomplete,
        )

    def _previous_period_metrics(
        self, ticker: str, current_key: PeriodKey
    ) -> list[MetricRow] | None:
        """Find the period immediately preceding ``current_key`` (same ticker)."""
        all_rows = self.metrics_repo.list_for_ticker(ticker)
        per_period: dict[PeriodKey, list[MetricRow]] = defaultdict(list)
        for row in all_rows:
            key = (row.reporting_date, row.period_type, row.reporting_standard)
            if key == current_key:
                continue
            per_period[key].append(row)
        if not per_period:
            return None
        # Closest earlier reporting_date with the same period_type and
        # reporting_standard if available, otherwise just the latest earlier
        # period regardless.
        same_shape = [
            key
            for key in per_period
            if key[1] == current_key[1]
            and key[2] == current_key[2]
            and key[0] < current_key[0]
        ]
        if same_shape:
            chosen = max(same_shape, key=lambda k: k[0])
            return per_period[chosen]
        return None

    def _attach_qa_warnings(
        self,
        metrics: list[MetricRow],
        warnings: list[QAWarning],
    ) -> int:
        flagged = 0
        for metric in metrics:
            relevant = [
                {"code": w.code, "message": w.message}
                for w in warnings
                if not w.affected_metrics or metric.metric_name in w.affected_metrics
            ]
            payload = json.dumps(relevant, ensure_ascii=False) if relevant else None
            if payload != metric.qa_warning:
                self.metrics_repo.update_qa_warning(metric.metric_id, payload)
            if payload is not None:
                flagged += 1
        return flagged


def _group_by_period(
    metrics: list[MetricRow],
) -> dict[PeriodKey, list[MetricRow]]:
    out: dict[PeriodKey, list[MetricRow]] = defaultdict(list)
    for m in metrics:
        key = (m.reporting_date, m.period_type, m.reporting_standard)
        out[key].append(m)
    return dict(out)
