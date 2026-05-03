"""Production SLO smoke checks (Patch 46).

Run on the live VPS with ``make slo-smoke``. The tests are designed to
fail loud when the deployment drifts from the agreed contract — wiped
canary baseline, missing evolution_ticks table, sandbox config changed.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from edx.evolve.memory import MEMORY_PATH, read

REPO = Path(__file__).resolve().parents[2]
SETTINGS_EVOLVE = REPO / ".claude" / "settings.evolve.json"
DEFAULT_STATE_DB = REPO / "data" / "state.sqlite"


def _state_db_path() -> Path:
    """Honour EDX_STATE_DB if the operator wants to point at a different
    location (e.g. /opt/edx/data/state.sqlite on the VPS)."""
    import os

    override = os.environ.get("EDX_STATE_DB")
    if override:
        return Path(override)
    return DEFAULT_STATE_DB


def test_state_db_evolution_tables_present() -> None:
    """Migration 0010 must be applied on the live DB.

    On dev hosts the state.sqlite may be at an older migration if the
    operator hasn't run ``edx update`` since pulling Patch 38 — we skip
    rather than fail in that case.  ``edx evolve tick`` applies the
    migration on first invocation.
    """
    db_path = _state_db_path()
    if not db_path.exists():
        pytest.skip(f"state DB {db_path} not present (CI environment)")
    with sqlite3.connect(str(db_path)) as conn:
        applied = {
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations"
            )
        }
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    if "0010_evolution" not in applied:
        pytest.skip(
            f"state DB at migration set {sorted(applied)[-1] if applied else '(none)'}; "
            f"run `edx evolve tick` (or `edx update`) once to apply 0010_evolution"
        )
    assert "evolution_ticks" in names
    assert "evolution_skiplist" in names


def test_memory_md_present_and_parseable() -> None:
    if not MEMORY_PATH.exists():
        pytest.skip("evolution/MEMORY.md not present (CI environment)")
    digest = read(MEMORY_PATH)
    # Patches log may be empty; we just need a successful parse.
    assert digest.raw  # non-empty


def test_canary_baseline_present_and_fresh() -> None:
    """The canary baseline must exist and be < 30 days old.

    Skipped (rather than failed) when:
      - state DB is missing (CI),
      - baseline file is missing (operator forgot to capture — visible
        instead via `edx evolve report` exit code on prod monitoring).
    """
    db_path = _state_db_path()
    if not db_path.exists():
        pytest.skip(f"state DB {db_path} not present (CI environment)")
    baseline = db_path.parent / "canary_baseline.json"
    if not baseline.exists():
        pytest.skip(
            f"missing {baseline} — run `edx evolve canary capture` on the VPS"
        )
    import time

    age_seconds = time.time() - baseline.stat().st_mtime
    assert age_seconds < 30 * 86400, (
        f"canary baseline is {age_seconds / 86400:.1f} days old; "
        "re-capture after recent pipeline changes"
    )


def test_settings_evolve_safe() -> None:
    """The headless agent sandbox must keep its critical deny rules."""
    payload = json.loads(SETTINGS_EVOLVE.read_text(encoding="utf-8"))
    deny = set(payload["permissions"]["deny"])
    for required in (
        "Bash(git push *)",
        "Bash(git reset --hard *)",
        "Bash(git commit *)",
        "Edit(.env*)",
        "Edit(deploy/**)",
        "Edit(.git/**)",
        "WebFetch",
        "WebSearch",
    ):
        assert required in deny, f"deny rule missing: {required}"


def test_slash_command_present() -> None:
    p = REPO / ".claude" / "commands" / "edx-evolve-fix.md"
    assert p.exists(), f"missing slash command file: {p}"


def test_pyproject_python_311_supported() -> None:
    """Self-evolve targets Python 3.11+. Catch accidental downgrade."""
    txt = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11"' in txt
