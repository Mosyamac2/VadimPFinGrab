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
    skipped: int = 0,
    qa: int = 0,
    pubs_total: int | None = None,
) -> TickerSnapshot:
    by_status: dict[str, int] = {}
    if written:
        by_status["written"] = written
    if skipped:
        by_status["skipped"] = skipped
    total = pubs_total if pubs_total is not None else (written + skipped)
    return TickerSnapshot(
        ticker=ticker,
        publications_total=total,
        publications_by_status=by_status,
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


def test_verdict_neutral_when_bootstrapped_but_no_metrics_yet() -> None:
    """Company with publications in the DB but 0 metrics is neutral (still processing)."""
    before = _snap(metrics=0, written=0, pubs_total=5)
    after = _snap(metrics=0, written=0, pubs_total=5)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "neutral"


def test_verdict_ok_when_zero_publications_and_clean_run() -> None:
    """Company with zero total publications and a clean pipeline run is ok.

    Represents the stable no-data state: the company is registered on the
    portal but has no filed reports (discoverer found nothing across all
    4 type URLs, HTTP 200 + empty table). Marking as "ok" puts the ticker
    on the normal cooldown cycle instead of re-selecting every tick.
    """
    before = _snap(metrics=0, written=0)  # publications_total=0
    after = _snap(metrics=0, written=0)   # publications_total=0
    assert compute_verdict(before, after, pipeline_returncode=0).code == "ok"


def test_verdict_fail_when_zero_publications_and_nonzero_returncode() -> None:
    """Pipeline crash (rc!=0) with zero publications is still a fail."""
    before = _snap(metrics=0, written=0)
    after = _snap(metrics=0, written=0)
    assert compute_verdict(before, after, pipeline_returncode=1).code == "fail"


def test_verdict_neutral_when_publications_appeared_this_tick() -> None:
    """Company went from 0 to N publications this tick — neutral (no metrics yet)."""
    before = _snap(metrics=0, written=0)           # 0 pubs before
    after = _snap(metrics=0, written=0, pubs_total=5)  # 5 pubs discovered
    assert compute_verdict(before, after, pipeline_returncode=0).code == "neutral"


def test_verdict_ok_on_retry_when_all_written_no_metrics() -> None:
    """RETRY run: before.total>0, all pubs written, 0 metrics → ok.

    Covers documents that contain no financial figures (e.g. accounting
    policies). On the FIRST run, before.total=0 → neutral. On the RETRY
    run, before.total=1 → ok, so the improvement gate passes.
    """
    before = _snap(metrics=0, written=1, pubs_total=1)
    after = _snap(metrics=0, written=1, pubs_total=1)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "ok"


def test_verdict_neutral_on_first_run_all_written_no_metrics() -> None:
    """FIRST RUN: before.total==0 guards the branch — must stay neutral.

    This prevents bootstrapped companies from spuriously getting ok
    on their very first tick before any retry has occurred.
    """
    before = _snap(metrics=0, written=0, pubs_total=0)
    after = _snap(metrics=0, written=1, pubs_total=1)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "neutral"


def test_verdict_ok_all_written_no_metrics_does_not_fire_with_partial_written() -> None:
    """Not all publications written → branch must not fire → neutral."""
    before = _snap(metrics=0, written=1, pubs_total=2)
    after = _snap(metrics=0, written=1, pubs_total=2)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "neutral"


def test_verdict_ok_all_written_no_metrics_does_not_fire_when_metrics_exist() -> None:
    """publications written=1 but metrics=5 → standard ok-via-metrics branch fires."""
    before = _snap(metrics=5, written=1)
    after = _snap(metrics=5, written=1)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "ok"


def test_verdict_ok_when_mixed_written_skipped_no_metrics_on_retry() -> None:
    """RETRY: before.total>0, mix of written+skipped, 0 metrics → ok.

    Models EDX20321 (ОАО ЦСД): 19 written (scanned RSBU, LLM found 0 metrics)
    + 27 skipped (type-2 annual reports, no IFRS/RSBU/ISSUER docs). This is a
    stable terminal state — the all-terminal-no-metrics branch fires.
    """
    before = _snap(metrics=0, written=19, skipped=27)
    after = _snap(metrics=0, written=19, skipped=27)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "ok"


def test_verdict_neutral_on_first_run_mixed_written_skipped() -> None:
    """FIRST RUN: before.total==0 guard blocks the branch → stays neutral."""
    before = _snap(metrics=0, written=0, skipped=0, pubs_total=0)
    after = _snap(metrics=0, written=5, skipped=3)
    assert compute_verdict(before, after, pipeline_returncode=0).code == "neutral"


def test_verdict_ok_all_terminal_does_not_fire_when_some_not_terminal() -> None:
    """Mix of written + non-terminal (extracted) → branch must not fire → neutral."""
    before = _snap(metrics=0, written=1, pubs_total=2)
    after = _snap(metrics=0, written=1, pubs_total=2)
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
