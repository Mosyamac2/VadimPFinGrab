"""Canary baseline / check (Patch 41)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from edx.evolve.canaries import (
    CANARY_TICKERS,
    canary_baseline_path,
    check_canaries,
    load_canary_baseline,
    take_canary_baseline,
)
from edx.storage import Database


def _seed_full_canary(conn: sqlite3.Connection, ticker: str) -> None:
    conn.execute(
        "INSERT INTO tickers (ticker, e_disclosure_id, name, added_at) "
        "VALUES (?, ?, ?, ?)",
        (ticker, "1", f"Canary {ticker}", "2026-05-03T00:00:00"),
    )
    conn.execute(
        """
        INSERT INTO publications
            (publication_id, ticker, publication_type, publication_date,
             source_url, status, discovered_at, updated_at)
        VALUES (?, ?, 'report', '2026-04-01', 'https://x',
                'written', '2026-04-01', '2026-04-01')
        """,
        (f"P-{ticker}", ticker),
    )
    conn.execute(
        """
        INSERT INTO metrics (ticker, reporting_date, period_type, reporting_standard,
                             metric_name, value, currency, unit, extracted_at)
        VALUES (?, '2026-03-31','Q1','IFRS','revenue', 100, 'RUB','RUB','2026-04-02')
        """,
        (ticker,),
    )


def test_baseline_path_uses_state_db_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "state.sqlite"
    assert canary_baseline_path(db_path).parent == db_path.parent


def test_take_and_load_round_trip(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    for ticker in CANARY_TICKERS:
        _seed_full_canary(evolve_conn, ticker)
    target = tmp_path / "canary_baseline.json"
    take_canary_baseline(evolve_conn, target)
    loaded = load_canary_baseline(target)
    for ticker in CANARY_TICKERS:
        assert ticker in loaded
        assert loaded[ticker].publications_total == 1
        assert loaded[ticker].metrics_rows == 1


def test_check_passes_on_no_change(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    for ticker in CANARY_TICKERS:
        _seed_full_canary(evolve_conn, ticker)
    target = tmp_path / "canary_baseline.json"
    take_canary_baseline(evolve_conn, target)
    reports = check_canaries(evolve_conn, target)
    assert all(r.ok for r in reports)


def test_check_detects_metrics_drop(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    for ticker in CANARY_TICKERS:
        _seed_full_canary(evolve_conn, ticker)
    target = tmp_path / "canary_baseline.json"
    take_canary_baseline(evolve_conn, target)
    # Wipe SBER metrics → expect ok=False on SBER only.
    evolve_conn.execute("DELETE FROM metrics WHERE ticker = 'SBER'")
    reports = check_canaries(evolve_conn, target)
    by_ticker = {r.ticker: r for r in reports}
    assert by_ticker["SBER"].ok is False
    assert any("metrics" in n for n in by_ticker["SBER"].notes)
    assert by_ticker["LKOH"].ok is True
    assert by_ticker["IZNM"].ok is True


def test_check_baseline_missing_returns_ok_with_note(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    reports = check_canaries(
        evolve_conn, tmp_path / "absent_baseline.json"
    )
    assert all(r.ok for r in reports)
    assert all("baseline_missing" in r.notes for r in reports)


def test_check_detects_written_drop(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    for ticker in CANARY_TICKERS:
        _seed_full_canary(evolve_conn, ticker)
    target = tmp_path / "canary_baseline.json"
    take_canary_baseline(evolve_conn, target)
    evolve_conn.execute(
        "UPDATE publications SET status = 'failed' WHERE ticker = 'IZNM'"
    )
    reports = check_canaries(evolve_conn, target)
    by_ticker = {r.ticker: r for r in reports}
    assert by_ticker["IZNM"].ok is False
    assert any("written" in n for n in by_ticker["IZNM"].notes)
