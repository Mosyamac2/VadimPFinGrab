"""EvolutionRepo: tick lifecycle, daily cost aggregation, skiplist (Patch 38)."""

from __future__ import annotations

import sqlite3

import pytest

from edx.storage import Database, EvolutionRepo


def test_migration_applied(tmp_db: Database) -> None:
    """Migration 0010 must be present after migrate()."""
    with tmp_db.connect() as conn:
        versions = {
            row["version"]
            for row in conn.execute(
                "SELECT version FROM schema_migrations"
            )
        }
    assert "0010_evolution" in versions


def test_evolution_tables_created(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    table_names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "evolution_ticks" in table_names
    assert "evolution_skiplist" in table_names

    tick_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(evolution_ticks)")
    }
    assert {
        "tick_id",
        "started_at",
        "phase",
        "verdict",
        "batch_json",
        "claude_cost_usd",
        "commit_sha",
        "bundle_path",
    }.issubset(tick_cols)


def test_create_and_get_tick(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    tick_id = repo.create_tick(
        started_at="2026-05-03T10:00:00+00:00",
        phase="baseline",
        batch_json='[{"company_id":"1210","ticker":"EDX1210"}]',
    )
    assert tick_id >= 1

    fetched = repo.get_tick(tick_id)
    assert fetched is not None
    assert fetched.tick_id == tick_id
    assert fetched.phase == "baseline"
    assert fetched.verdict is None
    assert fetched.finished_at is None
    assert "EDX1210" in fetched.batch_json


def test_update_tick_partial_does_not_zero_others(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    tick_id = repo.create_tick(
        started_at="2026-05-03T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    repo.update_tick(
        tick_id,
        snaps_before_json='{"EDX1": {"metrics": 0}}',
        bundle_path="evolution/runs/1",
    )
    repo.update_tick(tick_id, phase="claude_code")

    row = repo.get_tick(tick_id)
    assert row is not None
    # Earlier-set fields must survive the second partial update.
    assert row.snaps_before_json == '{"EDX1": {"metrics": 0}}'
    assert row.bundle_path == "evolution/runs/1"
    assert row.phase == "claude_code"


def test_update_tick_no_fields_is_noop(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    tick_id = repo.create_tick(
        started_at="2026-05-03T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    # No keyword args at all — must not raise, must not change row.
    repo.update_tick(tick_id)
    row = repo.get_tick(tick_id)
    assert row is not None
    assert row.phase == "baseline"


def test_invalid_phase_rejected_by_check(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_tick(
            started_at="2026-05-03T10:00:00+00:00",
            phase="bogus_phase",  # type: ignore[arg-type]
            batch_json="[]",
        )


def test_latest_ticks_descending(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    ids = [
        repo.create_tick(
            started_at=f"2026-05-03T10:0{i}:00+00:00",
            phase="baseline",
            batch_json="[]",
        )
        for i in range(3)
    ]
    rows = repo.latest_ticks(limit=10)
    assert [r.tick_id for r in rows] == list(reversed(ids))


def test_daily_cost_usd_sums_only_matching_day(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    t1 = repo.create_tick(
        started_at="2026-05-03T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    t2 = repo.create_tick(
        started_at="2026-05-03T11:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    t3 = repo.create_tick(
        started_at="2026-05-04T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    repo.update_tick(
        t1,
        verdict="ok",
        claude_cost_usd=0.5,
        finished_at="2026-05-03T10:30:00+00:00",
    )
    repo.update_tick(
        t2,
        verdict="ok",
        claude_cost_usd=1.25,
        finished_at="2026-05-03T11:30:00+00:00",
    )
    repo.update_tick(
        t3,
        verdict="ok",
        claude_cost_usd=2.0,
        finished_at="2026-05-04T10:30:00+00:00",
    )
    assert repo.daily_cost_usd("2026-05-03") == pytest.approx(1.75)
    assert repo.daily_cost_usd("2026-05-04") == pytest.approx(2.0)
    assert repo.daily_cost_usd("2026-05-05") == 0.0


def test_daily_cost_usd_ignores_unfinished(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    t1 = repo.create_tick(
        started_at="2026-05-03T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    # Cost set but finished_at not — should NOT count towards the daily total.
    repo.update_tick(t1, claude_cost_usd=10.0)
    assert repo.daily_cost_usd("2026-05-03") == 0.0


def test_skiplist_bump_to_give_up_clamps_at_three(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    tick_id = repo.create_tick(
        started_at="2026-05-03T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    assert repo.bump_failure("1210", tick_id) == 1
    assert repo.bump_failure("1210", tick_id) == 2
    assert repo.bump_failure("1210", tick_id) == 3
    # Repeated bumps after threshold are idempotent.
    assert repo.bump_failure("1210", tick_id) == 3
    assert repo.bump_failure("1210", tick_id) == 3

    entry = repo.get_skiplist_entry("1210")
    assert entry is not None
    assert entry.failure_count == 3
    assert entry.reason == "give_up"


def test_skiplist_add_overlap_idempotent(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    repo.add_overlap("3043")
    repo.add_overlap("3043")
    repo.add_overlap("3043")
    entry = repo.get_skiplist_entry("3043")
    assert entry is not None
    assert entry.reason == "moex_overlap"
    assert entry.failure_count == 0


def test_skiplist_reset_returns_true_when_present(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    repo.add_overlap("1210")
    assert repo.is_in_skiplist("1210") is True
    assert repo.reset("1210") is True
    assert repo.is_in_skiplist("1210") is False
    # Second reset returns False.
    assert repo.reset("1210") is False


def test_skiplist_overlap_does_not_become_giveup_on_bump(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    """A MOEX-overlap company that somehow ends up in a tick keeps its
    reason after a bump_failure (it's already supposed to be skipped)."""
    repo = EvolutionRepo(tmp_db, conn)
    tick_id = repo.create_tick(
        started_at="2026-05-03T10:00:00+00:00",
        phase="baseline",
        batch_json="[]",
    )
    repo.add_overlap("3043")
    repo.bump_failure("3043", tick_id)
    entry = repo.get_skiplist_entry("3043")
    assert entry is not None
    assert entry.reason == "moex_overlap"
    assert entry.failure_count == 1


def test_get_skiplist_returns_alphabetical(
    tmp_db: Database, conn: sqlite3.Connection
) -> None:
    repo = EvolutionRepo(tmp_db, conn)
    repo.add_overlap("3043")
    repo.add_overlap("1210")
    repo.add_overlap("17")
    ids = [e.company_id for e in repo.get_skiplist()]
    assert ids == ["1210", "17", "3043"]
