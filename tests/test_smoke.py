"""Smoke tests for the project scaffolding (prompt 01)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_package_imports() -> None:
    import edx

    assert hasattr(edx, "__version__")
    assert isinstance(edx.__version__, str)
    assert edx.__version__


def test_cli_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "edx.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "update" in result.stdout
    assert "run" in result.stdout


def test_logging_setup_creates_log_file(tmp_path: Path) -> None:
    from edx import logging_setup

    log_dir = tmp_path / "logs"
    logging_setup.configure(log_dir=log_dir)

    log = logging_setup.get_logger("edx.tests")
    log.info("smoke_test_event", payload="hello")

    log_file = log_dir / logging_setup.DEFAULT_LOG_FILE
    assert log_file.exists(), "RotatingFileHandler must create the log file"

    contents = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert contents, "log file should have at least one line"

    parsed = json.loads(contents[-1])
    assert parsed.get("event") == "smoke_test_event"
    assert parsed.get("payload") == "hello"
    assert parsed.get("level") == "info"
