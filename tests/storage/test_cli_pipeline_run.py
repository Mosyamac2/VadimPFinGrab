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


def _make_isolated_workspace(tmp_path: Path, *, empty_tickers: bool = False) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        (cfg / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    if empty_tickers:
        (cfg / "tickers.yaml").write_text("tickers: []\n", encoding="utf-8")
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
    """Hermetic CLI smoke: empty tickers + fake LLM key so we never touch the network."""
    cfg_dir = _make_isolated_workspace(tmp_path, empty_tickers=True)
    state_db = tmp_path / "data" / "state.sqlite"

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "fake-test-key"
    result = subprocess.run(
        [sys.executable, "-m", "edx.cli", "--config-dir", str(cfg_dir), "update"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert state_db.exists()

    events = [
        json.loads(line)
        for line in result.stdout.strip().splitlines()
        if line.startswith("{")
    ]
    assert any(e.get("event") == "migration_applied" and e.get("version") == "0001_init"
               for e in events)
    assert any(
        e.get("event") == "orchestrator_run_finished"
        and e.get("status") == "succeeded"
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
        # No tickers configured ⇒ no publications discovered.
        assert stats["publications_total"] == 0
        assert stats["new_publications"] == 0
    finally:
        conn.close()


def test_edx_update_idempotent_second_run(tmp_path: Path) -> None:
    cfg_dir = _make_isolated_workspace(tmp_path, empty_tickers=True)
    state_db = tmp_path / "data" / "state.sqlite"

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "fake-test-key"

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "edx.cli", "--config-dir", str(cfg_dir), "update"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            cwd=tmp_path,
        )

    first = _run()
    assert first.returncode == 0
    second = _run()
    assert second.returncode == 0

    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    try:
        runs_count = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        migration_count = conn.execute(
            "SELECT COUNT(*) AS c FROM schema_migrations"
        ).fetchone()["c"]
    finally:
        conn.close()

    assert runs_count == 2
    assert migration_count >= 1
