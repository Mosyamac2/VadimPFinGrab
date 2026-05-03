"""Headless Claude Code runner with budget + timeout enforcement (Patch 42).

We spawn ``claude -p "/edx-evolve-fix N" --output-format stream-json``,
read the JSONL line-by-line, and SIGTERM the process if either the
per-tick cost cap or the max_turns budget is exceeded mid-stream.

The runner NEVER raises on agent error — it returns
``ClaudeRunResult(is_error=True, …)``. This keeps the tick orchestrator
control flow simple: budget overruns and missing tokens look the same as
a failed agent invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

DEFAULT_TIMEOUT_S: Final[int] = 30 * 60
DEFAULT_BUDGET_USD: Final[float] = 2.0
DEFAULT_MAX_TURNS: Final[int] = 25
TOKEN_ENV_VAR: Final[str] = "CLAUDE_CODE_OAUTH_TOKEN"


@dataclass(frozen=True, slots=True)
class ClaudeRunResult:
    session_id: str | None
    is_error: bool
    cost_usd: float
    turns: int
    duration_seconds: float
    modified_files: tuple[str, ...]
    stream_path: Path
    summary_path: Path | None
    last_assistant_text: str
    error_summary: str | None


def run_agent(
    *,
    bundle_dir: Path,
    tick_id: int,
    project_root: Path,
    budget_usd: float = DEFAULT_BUDGET_USD,
    max_turns: int = DEFAULT_MAX_TURNS,
    timeout_seconds: int = DEFAULT_TIMEOUT_S,
    claude_executable: str | None = None,
) -> ClaudeRunResult:
    """Run ``claude -p`` and stream its output into ``bundle_dir/claude.jsonl``.

    Returns a snapshot of cost / turns / modified files after the run.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    stream_path = bundle_dir / "claude.jsonl"
    summary_path = bundle_dir / "SUMMARY.md"

    if not os.environ.get(TOKEN_ENV_VAR):
        return _empty_error_result(
            stream_path,
            error_summary=f"{TOKEN_ENV_VAR} not set",
        )

    binary = claude_executable or shutil.which("claude")
    if binary is None:
        return _empty_error_result(
            stream_path,
            error_summary="claude binary not found in PATH",
        )

    head_sha_before = _git_head(project_root)

    argv = [
        binary,
        "-p",
        f"/edx-evolve-fix {tick_id}",
        "--output-format",
        "stream-json",
        # ``--print --output-format=stream-json`` requires ``--verbose``
        # in current Claude Code versions; without it the binary refuses
        # to start with a hard error to stderr (caught in pilot, all
        # live ticks pre-fix exited with code 1 / cost=0).
        "--verbose",
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        "acceptEdits",
        "--add-dir",
        str(bundle_dir.resolve()),
    ]

    started = time.monotonic()
    cost_usd: float = 0.0
    turns: int = 0
    session_id: str | None = None
    is_error = False
    last_assistant_text = ""
    error_summary: str | None = None

    # Patch fix (post-pilot): strip Anthropic API auth vars so claude
    # falls through to CLAUDE_CODE_OAUTH_TOKEN. systemd loads BOTH
    # /opt/edx/.env (pipeline) and /opt/edx/.env.evolve (agent) — and
    # claude's auth precedence puts ANTHROPIC_API_KEY ABOVE
    # CLAUDE_CODE_OAUTH_TOKEN, so the pipeline's API key wins and
    # claude gets 403 (the key has no direct-API rights for
    # claude-sonnet-4-6). The pipeline subprocess builds its own env
    # downstream so this strip doesn't break it.
    child_env = os.environ.copy()
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        child_env.pop(key, None)

    try:
        with subprocess.Popen(
            argv,
            cwd=str(project_root),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        ) as proc:
            assert proc.stdout is not None
            with stream_path.open("w", encoding="utf-8") as out_fh:
                for line in proc.stdout:
                    out_fh.write(line)
                    out_fh.flush()
                    parsed = _parse_event(line)
                    if parsed is None:
                        continue
                    cost_usd, turns, session_id, last_assistant_text, is_error = (
                        _absorb_event(
                            parsed,
                            cost_usd=cost_usd,
                            turns=turns,
                            session_id=session_id,
                            last_assistant_text=last_assistant_text,
                            is_error=is_error,
                        )
                    )
                    classified = _classify_result_error(parsed)
                    if classified is not None and error_summary is None:
                        error_summary = classified
                    if cost_usd > budget_usd:
                        proc.terminate()
                        is_error = True
                        error_summary = (
                            f"budget cap exceeded: ${cost_usd:.3f} > ${budget_usd:.2f}"
                        )
                        break
                    if turns > max_turns:
                        proc.terminate()
                        is_error = True
                        error_summary = (
                            f"max_turns exceeded: {turns} > {max_turns}"
                        )
                        break
                    if (time.monotonic() - started) > timeout_seconds:
                        proc.terminate()
                        is_error = True
                        error_summary = "wall-time timeout"
                        break
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if not is_error and proc.returncode not in (0, None):
                is_error = True
                error_summary = error_summary or f"agent exit={proc.returncode}"
    except (OSError, subprocess.SubprocessError) as exc:
        is_error = True
        error_summary = f"subprocess error: {exc!r}"

    duration = time.monotonic() - started
    modified_files = _collect_modified_files(project_root, head_sha_before)

    return ClaudeRunResult(
        session_id=session_id,
        is_error=is_error,
        cost_usd=cost_usd,
        turns=turns,
        duration_seconds=duration,
        modified_files=modified_files,
        stream_path=stream_path,
        summary_path=summary_path if summary_path.exists() else None,
        last_assistant_text=last_assistant_text[-2048:],
        error_summary=error_summary,
    )


def _parse_event(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _absorb_event(
    event: dict[str, Any],
    *,
    cost_usd: float,
    turns: int,
    session_id: str | None,
    last_assistant_text: str,
    is_error: bool,
) -> tuple[float, int, str | None, str, bool]:
    """Pull cost / turns / session_id / last text out of a stream-json event."""

    event_type = event.get("type")

    if event_type == "result":
        cost = _coerce_float(event.get("total_cost_usd"))
        if cost is not None and cost > cost_usd:
            cost_usd = cost
        num_turns = _coerce_int(event.get("num_turns"))
        if num_turns is not None and num_turns > turns:
            turns = num_turns
        sid = event.get("session_id")
        if isinstance(sid, str):
            session_id = sid
        if event.get("is_error") is True:
            is_error = True

    elif event_type == "system":
        sid = event.get("session_id")
        if isinstance(sid, str) and not session_id:
            session_id = sid

    elif event_type == "assistant":
        turns += 1
        text = _extract_assistant_text(event)
        if text:
            last_assistant_text = text

    return cost_usd, turns, session_id, last_assistant_text, is_error


def _classify_result_error(event: dict[str, Any]) -> str | None:
    """Map a stream-json ``result`` event into a precise error_summary.

    Returns ``None`` for non-error or unrecognised shapes; the caller falls
    back to the generic ``claude_run_error`` produced by the orchestrator.
    """
    if event.get("type") != "result" or event.get("is_error") is not True:
        return None
    status = event.get("api_error_status")
    if isinstance(status, int) and status == 403:
        # Distinct from auth-precedence failures: the request reached
        # Anthropic but was refused. On this VPS the usual cause is the
        # systemd unit not propagating HTTPS_PROXY to the agent
        # subprocess, so direct egress to api.anthropic.com is blocked.
        return "auth_failed_403"
    if isinstance(status, int):
        return f"api_error_{status}"
    subtype = event.get("subtype")
    if subtype == "error_max_turns":
        return "agent_max_turns"
    return None


def _extract_assistant_text(event: dict[str, Any]) -> str:
    msg = event.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _git_head(project_root: Path) -> str | None:
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return sha or None


def _collect_modified_files(
    project_root: Path, head_sha_before: str | None
) -> tuple[str, ...]:
    """`git diff --name-only HEAD` plus untracked files."""
    files: set[str] = set()
    for argv in (
        ["git", "-C", str(project_root), "diff", "--name-only"],
        [
            "git",
            "-C",
            str(project_root),
            "ls-files",
            "--others",
            "--exclude-standard",
        ],
    ):
        try:
            out = subprocess.check_output(
                argv, text=True, stderr=subprocess.DEVNULL
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
        for line in out.splitlines():
            line = line.strip()
            if line:
                files.add(line)
    return tuple(sorted(files))


def _empty_error_result(
    stream_path: Path, *, error_summary: str
) -> ClaudeRunResult:
    if not stream_path.exists():
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        stream_path.write_text("", encoding="utf-8")
    return ClaudeRunResult(
        session_id=None,
        is_error=True,
        cost_usd=0.0,
        turns=0,
        duration_seconds=0.0,
        modified_files=(),
        stream_path=stream_path,
        summary_path=None,
        last_assistant_text="",
        error_summary=error_summary,
    )


__all__ = [
    "ClaudeRunResult",
    "DEFAULT_BUDGET_USD",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_S",
    "TOKEN_ENV_VAR",
    "run_agent",
]
