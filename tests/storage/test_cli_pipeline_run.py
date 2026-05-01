"""End-to-end CLI integration: edx update creates state.sqlite + runs row."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG_DIR = REPO_ROOT / "config"


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "edx.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
        cwd=cwd,
    )


def _make_isolated_workspace(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        (cfg / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    # Redirect state DB and other paths under tmp_path so the test stays isolated
    app_path = cfg / "app.yaml"
    app = yaml.safe_load(app_path.read_text(encoding="utf-8"))
    app["paths"]["data_dir"] = str(tmp_path / "data")
    app["paths"]["raw_dir"] = str(tmp_path / "data" / "raw")
    app["paths"]["processed_dir"] = str(tmp_path / "data" / "processed")
    app["paths"]["state_db"] = str(tmp_path / "data" / "state.sqlite")
    app["paths"]["output_dir"] = str(tmp_path / "output")
    app["paths"]["excel_path"] = str(tmp_path / "output" / "e-disclosure.xlsx")
    app["paths"]["logs_dir"] = str(tmp_path / "logs")
    app_path.write_text(yaml.safe_dump(app), encoding="utf-8")
    return cfg


def test_edx_update_creates_state_db_and_runs_row(tmp_path: Path) -> None:
    cfg_dir = _make_isolated_workspace(tmp_path)
    state_db = tmp_path / "data" / "state.sqlite"

    result = _run_cli(["--config-dir", str(cfg_dir), "update"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr or result.stdout

    assert state_db.exists()

    events = [
        json.loads(line)
        for line in result.stdout.strip().splitlines()
        if line.startswith("{")
    ]
    migration_events = [e for e in events if e.get("event") == "migration_applied"]
    assert any(e.get("version") == "0001_init" for e in migration_events)
    assert any(e.get("event") == "tickers_synced" for e in events)
    assert any(
        e.get("event") == "run_finished" and e.get("status") == "succeeded"
        for e in events
    )

    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    try:
        runs = list(conn.execute("SELECT * FROM runs"))
        assert len(runs) == 1
        assert runs[0]["status"] == "succeeded"
        assert runs[0]["mode"] == "update"
        stats = json.loads(runs[0]["stats_json"])
        assert stats["ticker_count"] == 3
        assert stats["migrations_applied"] == ["0001_init"]

        tickers = list(conn.execute("SELECT ticker FROM tickers ORDER BY ticker"))
        assert [r["ticker"] for r in tickers] == ["GAZP", "LKOH", "SBER"]
    finally:
        conn.close()


def test_edx_update_idempotent_second_run(tmp_path: Path) -> None:
    cfg_dir = _make_isolated_workspace(tmp_path)
    state_db = tmp_path / "data" / "state.sqlite"

    first = _run_cli(["--config-dir", str(cfg_dir), "update"], cwd=tmp_path)
    assert first.returncode == 0

    second = _run_cli(["--config-dir", str(cfg_dir), "update"], cwd=tmp_path)
    assert second.returncode == 0

    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    try:
        runs_count = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        ticker_count = conn.execute("SELECT COUNT(*) AS c FROM tickers").fetchone()["c"]
        migration_count = conn.execute(
            "SELECT COUNT(*) AS c FROM schema_migrations"
        ).fetchone()["c"]
    finally:
        conn.close()

    assert runs_count == 2
    assert ticker_count == 3
    assert migration_count == 1
