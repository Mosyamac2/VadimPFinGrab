"""Per-ticker verdict computation (Patch 40).

Compares before/after :class:`TickerSnapshot` pairs and returns a coarse
classification used by the tick orchestrator: ``ok | neutral | regression
| fail``. The aggregate over a batch lives in :func:`aggregate_verdict`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from edx.evolve.snapshot import TickerSnapshot

VerdictCode = Literal["ok", "neutral", "regression", "fail"]

DEFAULT_MIN_METRICS_FOR_OK: Final[int] = 1


@dataclass(frozen=True, slots=True)
class TickerVerdict:
    ticker: str
    code: VerdictCode
    metrics_delta: int
    publications_written_delta: int
    qa_issues_delta: int
    notes: tuple[str, ...]


def compute_verdict(
    before: TickerSnapshot,
    after: TickerSnapshot,
    *,
    pipeline_returncode: int,
    min_metrics_for_ok: int = DEFAULT_MIN_METRICS_FOR_OK,
) -> TickerVerdict:
    """Coarse improvement/regression classification.

    Logic (priority order):
      - returncode != 0 AND metrics_delta == 0 → ``fail``.
      - metrics_after < metrics_before → ``regression``.
      - publications_written_delta < 0 → ``regression`` (a previously
        ``written`` row decayed to ``failed`` / ``skipped``).
      - metrics_delta >= ``min_metrics_for_ok`` AND
        publications_written_delta >= 0 → ``ok``.
      - otherwise → ``neutral`` (no harm, no benefit yet).
    """
    if before.ticker != after.ticker:
        raise ValueError(
            f"snapshot ticker mismatch: {before.ticker} vs {after.ticker}"
        )

    metrics_delta = after.metrics_rows - before.metrics_rows
    written_delta = after.publications_by_status.get("written", 0) - (
        before.publications_by_status.get("written", 0)
    )
    qa_delta = after.qa_issues_count - before.qa_issues_count

    notes: list[str] = []
    if metrics_delta:
        notes.append(f"metrics {metrics_delta:+d}")
    if written_delta:
        notes.append(f"publications.written {written_delta:+d}")
    if qa_delta:
        notes.append(f"qa_issues {qa_delta:+d}")
    if pipeline_returncode != 0:
        notes.append(f"returncode={pipeline_returncode}")

    code: VerdictCode
    if pipeline_returncode != 0 and metrics_delta == 0:
        code = "fail"
    elif metrics_delta < 0 or written_delta < 0:
        code = "regression"
    elif metrics_delta >= min_metrics_for_ok and written_delta >= 0:
        code = "ok"
    else:
        code = "neutral"

    return TickerVerdict(
        ticker=after.ticker,
        code=code,
        metrics_delta=metrics_delta,
        publications_written_delta=written_delta,
        qa_issues_delta=qa_delta,
        notes=tuple(notes),
    )


def aggregate_verdict(
    verdicts: dict[str, TickerVerdict],
) -> VerdictCode:
    """Worst-case rollup over a batch's per-ticker verdicts."""
    if not verdicts:
        return "fail"
    codes = {v.code for v in verdicts.values()}
    if "regression" in codes:
        return "regression"
    if "fail" in codes:
        return "fail"
    if codes == {"ok"}:
        return "ok"
    return "neutral"


__all__ = [
    "TickerVerdict",
    "VerdictCode",
    "aggregate_verdict",
    "compute_verdict",
]
