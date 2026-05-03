"""Subprocess runner for one ``edx update`` invocation on a batch (Patch 40).

The runner is a thin shell over ``subprocess.run``: it builds the argv,
plumbs ``EDX_LOG_FILE`` so structured logs land in the bundle directory,
caps wall-time, and returns trimmed stdout/stderr tails for the bundle.
The full structured log lives in ``log_path``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_TIMEOUT_S: Final[int] = 30 * 60
TAIL_BYTES: Final[int] = 4 * 1024


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    returncode: int
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str
    log_path: Path
    timed_out: bool = False


def edx_executable() -> Path:
    """Locate the ``edx`` console script next to the current ``python``.

    In the pinned ``.venv`` layout, ``sys.executable`` is
    ``<root>/.venv/bin/python`` and the entry point is ``<root>/.venv/bin/edx``.
    """
    return Path(sys.executable).parent / "edx"


def run_pipeline_on_batch(
    tickers: list[str],
    *,
    config_dir: Path = Path("config-evolve"),
    log_path: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_S,
    extra_env: dict[str, str] | None = None,
) -> PipelineRunResult:
    """Run ``edx update --config-dir CFG --ticker T1 --ticker T2 ...``.

    The child process writes structured logs to ``log_path`` via
    ``EDX_LOG_FILE``. Stdout/stderr tails are captured for the bundle.

    On ``subprocess.TimeoutExpired`` returncode is set to -1 and
    ``timed_out=True``; we never re-raise so the calling tick can
    record the verdict and move on.
    """
    if not tickers:
        raise ValueError("run_pipeline_on_batch requires at least one ticker")

    log_path.parent.mkdir(parents=True, exist_ok=True)

    argv: list[str] = [
        str(edx_executable()),
        "update",
        "--config-dir",
        str(config_dir),
    ]
    for ticker in tickers:
        argv.extend(["--ticker", ticker])

    env = {**os.environ, "EDX_LOG_FILE": str(log_path)}
    if extra_env:
        env.update(extra_env)

    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        return PipelineRunResult(
            returncode=-1,
            duration_seconds=duration,
            stdout_tail=_tail(exc.stdout if isinstance(exc.stdout, str) else None),
            stderr_tail=_tail(exc.stderr if isinstance(exc.stderr, str) else None),
            log_path=log_path,
            timed_out=True,
        )

    duration = time.monotonic() - started
    return PipelineRunResult(
        returncode=int(completed.returncode),
        duration_seconds=duration,
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
        log_path=log_path,
        timed_out=False,
    )


def _tail(text: str | None) -> str:
    if not text:
        return ""
    if len(text) <= TAIL_BYTES:
        return text
    return text[-TAIL_BYTES:]


__all__ = ["PipelineRunResult", "edx_executable", "run_pipeline_on_batch"]
