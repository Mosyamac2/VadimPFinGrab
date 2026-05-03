"""evolve.cleanup: purge_old_runs + duration parser (Patch 46)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from edx.evolve.cleanup import (
    CleanupResult,
    parse_duration,
    purge_old_runs,
)
from edx.storage import EvolutionRepo


def _make_bundle(runs_dir: Path, tick_id: int) -> Path:
    bundle = runs_dir / str(tick_id)
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "pipeline.log").write_text("dummy\n", encoding="utf-8")
    (bundle / "manifest.json").write_text("{}", encoding="utf-8")
    return bundle


def _create_finished_tick(
    repo: EvolutionRepo,
    *,
    finished_at: str,
    verdict: str = "ok",
    started_at: str | None = None,
) -> int:
    tid = repo.create_tick(
        started_at=started_at or finished_at,
        phase="baseline",
        batch_json="[]",
    )
    repo.update_tick(tid, verdict=verdict, finished_at=finished_at)  # type: ignore[arg-type]
    return tid


def test_parse_duration_variants() -> None:
    assert parse_duration("30d") == timedelta(days=30)
    assert parse_duration("12h") == timedelta(hours=12)
    assert parse_duration("45m") == timedelta(minutes=45)
    assert parse_duration("2w") == timedelta(weeks=2)
    assert parse_duration("90s") == timedelta(seconds=90)


def test_parse_duration_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_duration("forever")
    with pytest.raises(ValueError):
        parse_duration("12X")


def test_purge_old_runs_removes_old_ok(
    evolve_repo: EvolutionRepo, tmp_path: Path
) -> None:
    runs = tmp_path / "evolution" / "runs"
    now = datetime(2026, 5, 4, tzinfo=UTC)

    # Old finished tick — should be removed.
    tid_old = _create_finished_tick(
        evolve_repo, finished_at="2026-04-01T10:00:00+00:00"
    )
    _make_bundle(runs, tid_old)

    # Recent tick — should be kept.
    tid_recent = _create_finished_tick(
        evolve_repo, finished_at="2026-05-03T10:00:00+00:00"
    )
    _make_bundle(runs, tid_recent)

    res = purge_old_runs(
        runs,
        repo=evolve_repo,
        older_than=timedelta(days=14),
        now=now,
    )
    assert res.removed == 1
    assert res.kept == 1
    assert not (runs / str(tid_old)).exists()
    assert (runs / str(tid_recent)).exists()


def test_purge_old_runs_keep_failed_preserves_non_ok(
    evolve_repo: EvolutionRepo, tmp_path: Path
) -> None:
    runs = tmp_path / "evolution" / "runs"
    now = datetime(2026, 5, 4, tzinfo=UTC)

    tid_ok = _create_finished_tick(
        evolve_repo, finished_at="2026-04-01T10:00:00+00:00", verdict="ok"
    )
    _make_bundle(runs, tid_ok)

    tid_fail = _create_finished_tick(
        evolve_repo, finished_at="2026-04-01T10:00:00+00:00", verdict="fail"
    )
    _make_bundle(runs, tid_fail)

    res = purge_old_runs(
        runs,
        repo=evolve_repo,
        older_than=timedelta(days=14),
        keep_failed=True,
        now=now,
    )
    assert res.removed == 1  # ok tick removed
    assert res.kept == 1     # failed tick kept
    assert not (runs / str(tid_ok)).exists()
    assert (runs / str(tid_fail)).exists()


def test_purge_old_runs_skips_unknown_dirs(
    evolve_repo: EvolutionRepo, tmp_path: Path
) -> None:
    runs = tmp_path / "evolution" / "runs"
    runs.mkdir(parents=True)
    # Non-numeric directory name: not a tick.
    (runs / "manual_inspection").mkdir()
    (runs / "manual_inspection" / "notes.md").write_text("op note\n")

    res = purge_old_runs(
        runs,
        repo=evolve_repo,
        older_than=timedelta(days=1),
    )
    assert res.removed == 0
    assert (runs / "manual_inspection").exists()


def test_purge_old_runs_handles_missing_runs_dir(
    evolve_repo: EvolutionRepo, tmp_path: Path
) -> None:
    res = purge_old_runs(
        tmp_path / "absent" / "runs",
        repo=evolve_repo,
        older_than=timedelta(days=1),
    )
    assert res == CleanupResult(removed=0, kept=0, removed_bytes=0)


def test_purge_old_runs_keeps_unfinished_ticks(
    evolve_repo: EvolutionRepo, tmp_path: Path
) -> None:
    runs = tmp_path / "evolution" / "runs"
    tid = evolve_repo.create_tick(
        started_at="2026-04-01T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    # No finished_at — should be kept regardless of age.
    _make_bundle(runs, tid)
    res = purge_old_runs(
        runs,
        repo=evolve_repo,
        older_than=timedelta(days=1),
    )
    assert res.removed == 0
    assert res.kept == 1
