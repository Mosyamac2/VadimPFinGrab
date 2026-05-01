"""RunsRepo: lifecycle and stats serialization."""

from __future__ import annotations

import json
import sqlite3

import pytest

from edx.storage import Database, RunsRepo


def test_start_and_finish_run(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = RunsRepo(tmp_db, conn)
    run_id = repo.start_run(mode="update")
    started = repo.get_by_id(run_id)
    assert started is not None
    assert started.status == "running"
    assert started.finished_at is None

    repo.finish_run(
        run_id,
        status="succeeded",
        stats={"publications_total": 7, "metrics_rows": 35},
    )
    finished = repo.get_by_id(run_id)
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.finished_at is not None
    assert finished.stats_json is not None
    parsed = json.loads(finished.stats_json)
    assert parsed["publications_total"] == 7


def test_finish_run_with_error_summary(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = RunsRepo(tmp_db, conn)
    run_id = repo.start_run(mode="full_reload")
    repo.finish_run(run_id, status="failed", error_summary="boom")
    finished = repo.get_by_id(run_id)
    assert finished is not None
    assert finished.status == "failed"
    assert finished.error_summary == "boom"


def test_invalid_run_status_raises(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = RunsRepo(tmp_db, conn)
    run_id = repo.start_run(mode="update")
    with pytest.raises(sqlite3.IntegrityError):
        repo.finish_run(run_id, status="weird")  # type: ignore[arg-type]


def test_latest_returns_in_descending_order(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = RunsRepo(tmp_db, conn)
    ids = [repo.start_run(mode="update") for _ in range(3)]
    rows = repo.latest(limit=10)
    assert [r.run_id for r in rows] == list(reversed(ids))
