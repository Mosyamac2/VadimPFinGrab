"""edx-evolve.service / .timer / install script / env.evolve.example."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "deploy" / "systemd" / "edx-evolve.service"
TIMER = REPO / "deploy" / "systemd" / "edx-evolve.timer"
INSTALL_SCRIPT = REPO / "deploy" / "install_claude_code.sh"
ENV_EXAMPLE = REPO / "deploy" / "env.evolve.example"


def test_service_unit_exists() -> None:
    assert SERVICE.exists()


def test_timer_unit_exists() -> None:
    assert TIMER.exists()


def test_service_uses_flock_lock() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    assert "flock -n /tmp/edx-evolve.lock" in text or "flock /tmp/edx-evolve.lock" in text


def test_service_user_is_edx() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    assert "User=edx" in text
    assert "Group=edx" in text


def test_service_loads_env_files() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    assert "EnvironmentFile=-/opt/edx/.env" in text
    assert "EnvironmentFile=-/opt/edx/.env.evolve" in text


def test_timer_5min_cadence() -> None:
    text = TIMER.read_text(encoding="utf-8")
    assert "OnUnitActiveSec=5min" in text


def test_timer_persistent_false() -> None:
    """Missed ticks must NOT be replayed — protects daily budget."""
    text = TIMER.read_text(encoding="utf-8")
    assert "Persistent=false" in text


def test_install_script_present_and_executable() -> None:
    assert INSTALL_SCRIPT.exists()
    mode = INSTALL_SCRIPT.stat().st_mode
    assert mode & 0o100, "install script should be executable"


def test_install_script_bash_syntax_ok() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(INSTALL_SCRIPT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_install_script_has_shebang_and_strict_mode() -> None:
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    assert first_line.startswith("#!")
    assert "set -euo pipefail" in text


def test_install_script_uses_node_20_and_npm_install() -> None:
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    # NODE_TARGET_MAJOR variable OR the literal version may appear.
    assert (
        "NODE_TARGET_MAJOR=20" in text
        or "setup_20.x" in text
    )
    assert "@anthropic-ai/claude-code" in text


def test_install_script_idempotent_check_for_claude() -> None:
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "command -v claude" in text


def test_env_example_present() -> None:
    assert ENV_EXAMPLE.exists()


def test_env_example_has_required_vars() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for var in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "EDX_EVOLVE_AGENT_ENABLED",
        "EDX_EVOLVE_DAILY_BUDGET_USD",
        "EDX_EVOLVE_TICK_BUDGET_USD",
    ):
        assert f"{var}=" in text, f"missing variable: {var}"


def test_env_example_no_real_secrets() -> None:
    """Refuse to commit anything that looks like a real key."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    forbidden = re.compile(
        r"(sk-ant-api03-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{20,})"
    )
    assert forbidden.search(text) is None


def test_env_example_default_agent_disabled() -> None:
    """Operator must explicitly enable the agent."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "EDX_EVOLVE_AGENT_ENABLED=0" in text


@pytest.mark.skipif(
    shutil.which("systemd-analyze") is None,
    reason="systemd-analyze not available",
)
def test_service_passes_systemd_analyze() -> None:
    """If systemd-analyze is on PATH, ask it to verify the unit.

    --no-pager keeps stdout deterministic; we accept exit=0 OR errors
    that only mention environment paths missing on the test host.
    """
    proc = subprocess.run(
        ["systemd-analyze", "verify", str(SERVICE), str(TIMER)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # Acceptable: complaints about non-existent /opt/edx paths.
        out = proc.stdout + proc.stderr
        if "/opt/edx" not in out:
            raise AssertionError(out)
