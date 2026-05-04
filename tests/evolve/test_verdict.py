"""compute_verdict and aggregate_verdict (Patch 40)."""

from __future__ import annotations

import pytest

from edx.evolve.snapshot import TickerSnapshot
from edx.evolve.verdict import (
    aggregate_verdict,
    compute_verdict,
)


def _snap(
    ticker: str = "EDX1",
    *,
    metrics: int = 0,
    written: int = 0,
    qa: int = 0,
) -> TickerSnapshot:
    return TickerSnapshot(
        ticker=ticker,
        publications_total=written,
        publications_by_status={"written": written} if written else {},
        documents_total=0,
        metrics_rows=metrics,
        metrics_by_standard={},
        qa_issues_count=qa,
        qa_issues_codes={},
        last_publication_date=None,
    )


def test_verdict_ok_when_metrics_grow_and_returncode_zero() -> None:
    before = _snap(metrics=0, written=0)
    after = _snap(metrics=5, written=2)
    v = compute_verdict(before, after, pipeline_returncode=0)
    assert v.code == "ok"
    assert v.metrics_delta == 5
    assert v.publications_written_delta == 2


def test_verdict_ok_when_already_healthy_and_no_change() -> None:
    """Already-healthy company (has metrics) with no new data this tick is ok."""
    before = _snap(metrics=3, written=1)
    after = _snap(metrics=3, written=1)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "ok"


def test_verdict_neutral_when_no_metrics_and_no_change() -> None:
    """Company with 0 metrics and no progress is neutral."""
    before = _snap(metrics=0, written=0)
    after = _snap(metrics=0, written=0)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "neutral"


def test_verdict_regression_on_metrics_drop() -> None:
    before = _snap(metrics=5, written=1)
    after = _snap(metrics=2, written=1)
    v = compute_verdict(before, after, pipeline_returncode=0)
    assert v.code == "regression"
    assert v.metrics_delta == -3


def test_verdict_regression_on_written_drop() -> None:
    before = _snap(metrics=0, written=2)
    after = _snap(metrics=0, written=1)
    assert (
        compute_verdict(before, after, pipeline_returncode=0).code == "regression"
    )


def test_verdict_fail_on_nonzero_returncode_no_progress() -> None:
    before = _snap(metrics=0, written=0)
    after = _snap(metrics=0, written=0)
    assert compute_verdict(before, after, pipeline_returncode=2).code == "fail"


def test_verdict_returncode_nonzero_but_metrics_grew_is_neutral() -> None:
    """Subprocess crashed at the very end but ETL got some metrics in.

    With our priority order: ``returncode != 0 AND metrics_delta == 0`` is
    fail; metrics_delta > 0 short-circuits past it. We classify as ``ok``
    because there's actual progress despite the non-zero exit. (The
    operator can investigate why the wrapper exited non-zero.)
    """
    before = _snap(metrics=0, written=0)
    after = _snap(metrics=2, written=1)
    assert compute_verdict(before, after, pipeline_returncode=2).code == "ok"


def test_verdict_mismatched_tickers_raises() -> None:
    before = _snap("EDX1", metrics=0)
    after = _snap("EDX2", metrics=0)
    with pytest.raises(ValueError, match="snapshot ticker mismatch"):
        compute_verdict(before, after, pipeline_returncode=0)


def test_aggregate_all_ok() -> None:
    a = _make_verdict("EDX1", "ok")
    b = _make_verdict("EDX2", "ok")
    assert aggregate_verdict({"EDX1": a, "EDX2": b}) == "ok"


def test_aggregate_any_regression_wins() -> None:
    a = _make_verdict("EDX1", "ok")
    b = _make_verdict("EDX2", "regression")
    c = _make_verdict("EDX3", "fail")
    out = aggregate_verdict({"EDX1": a, "EDX2": b, "EDX3": c})
    assert out == "regression"


def test_aggregate_fail_beats_neutral_and_ok() -> None:
    a = _make_verdict("EDX1", "ok")
    b = _make_verdict("EDX2", "neutral")
    c = _make_verdict("EDX3", "fail")
    out = aggregate_verdict({"EDX1": a, "EDX2": b, "EDX3": c})
    assert out == "fail"


def test_aggregate_neutral_when_mixed_neutral_and_ok() -> None:
    a = _make_verdict("EDX1", "ok")
    b = _make_verdict("EDX2", "neutral")
    out = aggregate_verdict({"EDX1": a, "EDX2": b})
    assert out == "neutral"


def test_aggregate_empty_is_fail() -> None:
    assert aggregate_verdict({}) == "fail"


def _make_verdict(ticker: str, code):  # type: ignore[no-untyped-def]
    from edx.evolve.verdict import TickerVerdict

    return TickerVerdict(
        ticker=ticker,
        code=code,
        metrics_delta=0,
        publications_written_delta=0,
        qa_issues_delta=0,
        notes=(),
    )
