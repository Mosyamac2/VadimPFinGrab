"""End-to-end CLI behaviour for config loading and `edx config check`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG_DIR = REPO_ROOT / "config"


def _run_cli(
    args: list[str],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "edx.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
        cwd=REPO_ROOT,
    )


def _events(stdout: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in stdout.strip().splitlines()
        if line.startswith("{")
    ]


def _find_event(stdout: str, name: str) -> dict[str, object]:
    for evt in _events(stdout):
        if evt.get("event") == name:
            return evt
    raise AssertionError(f"event {name!r} not found in stdout: {stdout!r}")


def _make_isolated_workspace_for_orchestrator(tmp_path: Path) -> Path:
    """Tmp config copy with empty tickers + redirected paths for hermetic CLI runs."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        (cfg_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    # Override tickers to empty so the orchestrator does no network I/O.
    (cfg_dir / "tickers.yaml").write_text("tickers: []\n", encoding="utf-8")
    # Redirect data/output paths into tmp_path.
    app = yaml.safe_load((cfg_dir / "app.yaml").read_text(encoding="utf-8"))
    app["paths"]["data_dir"] = str(tmp_path / "data")
    app["paths"]["raw_dir"] = str(tmp_path / "data" / "raw")
    app["paths"]["processed_dir"] = str(tmp_path / "data" / "processed")
    app["paths"]["state_db"] = str(tmp_path / "data" / "state.sqlite")
    app["paths"]["output_dir"] = str(tmp_path / "output")
    app["paths"]["excel_path"] = str(tmp_path / "output" / "e-disclosure.xlsx")
    app["paths"]["logs_dir"] = str(tmp_path / "logs")
    (cfg_dir / "app.yaml").write_text(yaml.safe_dump(app), encoding="utf-8")
    return cfg_dir


def test_cli_update_succeeds_with_reference_config(tmp_path: Path) -> None:
    cfg_dir = _make_isolated_workspace_for_orchestrator(tmp_path)
    result = _run_cli(
        ["--config-dir", str(cfg_dir), "update"],
        env={"ANTHROPIC_API_KEY": "fake-key-for-test"},
    )
    assert result.returncode == 0, result.stderr or result.stdout
    invoked = _find_event(result.stdout, "cli_command_invoked")
    assert invoked["command"] == "update"
    assert invoked["status"] == "succeeded"


def test_cli_run_full_reload_succeeds(tmp_path: Path) -> None:
    cfg_dir = _make_isolated_workspace_for_orchestrator(tmp_path)
    result = _run_cli(
        ["--config-dir", str(cfg_dir), "run", "--full-reload"],
        env={"ANTHROPIC_API_KEY": "fake-key-for-test"},
    )
    assert result.returncode == 0, result.stderr or result.stdout
    invoked = _find_event(result.stdout, "cli_command_invoked")
    assert invoked["command"] == "full_reload"
    assert invoked["status"] == "succeeded"


def test_cli_returns_exit_code_2_on_extra_field(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        (cfg_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    metrics = cfg_dir / "metrics.yaml"
    data = yaml.safe_load(metrics.read_text(encoding="utf-8"))
    data["extra_unknown_field"] = "boom"
    metrics.write_text(yaml.safe_dump(data), encoding="utf-8")

    result = _run_cli(["--config-dir", str(cfg_dir), "update"])
    assert result.returncode == 2
    parsed = _find_event(result.stdout, "config_load_failed")
    assert isinstance(parsed["file"], str) and parsed["file"].endswith("metrics.yaml")
    assert "extra_unknown_field" in (parsed.get("field") or "")


def test_cli_returns_exit_code_2_on_invalid_priority(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        (cfg_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    metrics = cfg_dir / "metrics.yaml"
    data = yaml.safe_load(metrics.read_text(encoding="utf-8"))
    data["reporting_priority"] = ["GAAP"]
    metrics.write_text(yaml.safe_dump(data), encoding="utf-8")

    result = _run_cli(["--config-dir", str(cfg_dir), "run"])
    assert result.returncode == 2


def test_cli_config_check_masks_secrets() -> None:
    result = _run_cli(
        ["config", "check"],
        env={"ANTHROPIC_API_KEY": "sk-do-not-leak-abcdef"},
    )
    assert result.returncode == 0, result.stderr
    assert "sk-do-not-leak-abcdef" not in result.stdout
    assert "anthropic_api_key: '***'" in result.stdout
    # The full settings tree is rendered.
    assert "metrics:" in result.stdout
    assert "event_types:" in result.stdout


def test_cli_config_check_json_format() -> None:
    result = _run_cli(
        ["config", "check", "--format", "json"],
        env={"OPENROUTER_API_KEY": "or-secret-xyz"},
    )
    assert result.returncode == 0, result.stderr
    assert "or-secret-xyz" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["secrets"]["openrouter_api_key"] == "***"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_cli_help_lists_config_subcommand(flag: str) -> None:
    result = _run_cli([flag])
    assert result.returncode == 0
    assert "config" in result.stdout
    assert "update" in result.stdout
    assert "run" in result.stdout


def test_cli_update_accepts_ticker_flag(tmp_path: Path) -> None:
    """edx update --ticker X must not fail with returncode=2 (argparse rejection)."""
    cfg_dir = _make_isolated_workspace_for_orchestrator(tmp_path)
    result = _run_cli(
        ["--config-dir", str(cfg_dir), "update", "--ticker", "SBER"],
        env={"ANTHROPIC_API_KEY": "fake-key-for-test"},
    )
    # returncode=2 means argparse rejected the argument — the bug this test guards.
    assert result.returncode != 2, (
        f"edx update rejected --ticker; stderr: {result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_cli_update_accepts_config_dir_after_subcommand(tmp_path: Path) -> None:
    """edx update --config-dir DIR --ticker X (evolve-wrapper order) must work."""
    cfg_dir = _make_isolated_workspace_for_orchestrator(tmp_path)
    result = _run_cli(
        ["update", "--config-dir", str(cfg_dir), "--ticker", "SBER"],
        env={"ANTHROPIC_API_KEY": "fake-key-for-test"},
    )
    assert result.returncode != 2, (
        f"edx update rejected --config-dir after subcommand; stderr: {result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr or result.stdout
