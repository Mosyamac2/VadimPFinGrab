"""Subprocess runner (Patch 40) — argv composition and env plumbing.

Никакого реального запуска пайплайна — все вызовы subprocess мокаются.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from edx.evolve import runner


class _FakeCompleted:
    def __init__(
        self, returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_runner_requires_at_least_one_ticker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one ticker"):
        runner.run_pipeline_on_batch(
            [], log_path=tmp_path / "p.log"
        )


def test_runner_argv_composition(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv, env, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        captured["timeout"] = timeout
        return _FakeCompleted(returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    log = tmp_path / "pipeline.log"
    res = runner.run_pipeline_on_batch(
        ["EDX1", "EDX2", "EDX3"],
        config_dir=Path("config-evolve"),
        log_path=log,
        timeout_seconds=900,
    )

    argv = captured["argv"]
    assert argv[0].endswith("edx")
    assert argv[1:5] == ["update", "--config-dir", "config-evolve", "--ticker"]
    # Three --ticker / VALUE pairs.
    ticker_args = argv[4:]
    assert ticker_args == [
        "--ticker", "EDX1", "--ticker", "EDX2", "--ticker", "EDX3"
    ]
    assert captured["env"]["EDX_LOG_FILE"] == str(log)
    assert captured["timeout"] == 900
    assert res.returncode == 0
    assert res.stdout_tail == "hello\n"
    assert res.timed_out is False
    assert res.log_path == log


def test_runner_extra_env_merged(monkeypatch, tmp_path: Path) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(argv, env, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        nonlocal captured_env
        captured_env = dict(env)
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner.run_pipeline_on_batch(
        ["EDX1"],
        log_path=tmp_path / "p.log",
        extra_env={"EDX_LOG_LEVEL": "DEBUG", "X": "1"},
    )
    assert captured_env["EDX_LOG_LEVEL"] == "DEBUG"
    assert captured_env["X"] == "1"
    # EDX_LOG_FILE always present.
    assert captured_env["EDX_LOG_FILE"].endswith("p.log")


def test_runner_handles_timeout(monkeypatch, tmp_path: Path) -> None:
    def fake_run(argv, env, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(
            cmd=argv, timeout=timeout, output="partial\n", stderr="warn\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = runner.run_pipeline_on_batch(
        ["EDX1"], log_path=tmp_path / "p.log", timeout_seconds=1
    )
    assert res.timed_out is True
    assert res.returncode == -1
    assert "partial" in res.stdout_tail
    assert "warn" in res.stderr_tail


def test_runner_creates_log_parent_dir(monkeypatch, tmp_path: Path) -> None:
    def fake_run(argv, env, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    log = tmp_path / "deep" / "nested" / "pipeline.log"
    runner.run_pipeline_on_batch(["EDX1"], log_path=log)
    assert log.parent.exists()


def test_runner_tail_truncation(monkeypatch, tmp_path: Path) -> None:
    huge = "a" * (runner.TAIL_BYTES * 3)

    def fake_run(argv, env, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        return _FakeCompleted(returncode=0, stdout=huge, stderr=huge)

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = runner.run_pipeline_on_batch(
        ["EDX1"], log_path=tmp_path / "p.log"
    )
    assert len(res.stdout_tail) == runner.TAIL_BYTES
    assert len(res.stderr_tail) == runner.TAIL_BYTES
