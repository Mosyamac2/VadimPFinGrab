"""claude_runner: token check + stream parsing + budget enforcement (Patch 42).

Никакого реального запуска ``claude`` — Popen полностью замокан.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edx.evolve import claude_runner as cr


class _FakePopen:
    """Минимальный stand-in для subprocess.Popen."""

    instances: list[_FakePopen] = []

    def __init__(
        self,
        argv,
        cwd=None,
        env=None,
        stdout=None,
        stderr=None,
        text=False,
        bufsize=0,
    ):  # type: ignore[no-untyped-def]
        _FakePopen.instances.append(self)
        self.argv = list(argv)
        self.cwd = cwd
        self.env = dict(env) if env is not None else None
        self._stdout_lines: list[str] = []
        self.stdout = self  # so the runner can iterate over us
        self.terminated = False
        self.killed = False
        self.returncode: int | None = None

    # context-manager protocol
    def __enter__(self) -> _FakePopen:
        return self

    def __exit__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    # iter protocol — feed our pre-canned lines line-by-line
    def __iter__(self):  # type: ignore[no-untyped-def]
        yield from list(self._stdout_lines)

    def feed(self, lines: list[str]) -> None:
        self._stdout_lines = lines

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 143

    def kill(self) -> None:
        self.killed = True
        self.returncode = 137

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.fixture(autouse=True)
def _isolate_token_env(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv(cr.TOKEN_ENV_VAR, raising=False)
    _FakePopen.instances.clear()


def _enable_claude(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(cr.TOKEN_ENV_VAR, "test-token")
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_claude.chmod(0o755)
    return fake_claude


def test_run_agent_no_token_returns_error(tmp_path: Path) -> None:
    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=1,
        project_root=tmp_path,
    )
    assert res.is_error is True
    assert res.error_summary == f"{cr.TOKEN_ENV_VAR} not set"
    assert res.cost_usd == 0.0
    assert res.modified_files == ()
    assert res.stream_path.exists()


def test_run_agent_no_claude_binary_returns_error(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(cr.TOKEN_ENV_VAR, "t")
    monkeypatch.setattr("shutil.which", lambda *_a, **_kw: None)
    # ALSO override claude_executable=None to force resolution path.
    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=1,
        project_root=tmp_path,
    )
    assert res.is_error is True
    assert "claude binary not found" in (res.error_summary or "")


def test_run_agent_parses_result_event(monkeypatch, tmp_path: Path) -> None:
    fake = _enable_claude(monkeypatch, tmp_path)

    monkeypatch.setattr(cr.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(cr, "_git_head", lambda _root: "abc")
    monkeypatch.setattr(
        cr, "_collect_modified_files", lambda *_a, **_kw: ("src/edx/foo.py",)
    )

    # Pre-fill the fake Popen with valid stream-json events.
    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        proc = _FakePopen(argv, **kwargs)
        proc.feed(
            [
                json.dumps(
                    {
                        "type": "system",
                        "session_id": "sess-001",
                    }
                )
                + "\n",
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "hello"}
                            ]
                        },
                    }
                )
                + "\n",
                json.dumps(
                    {
                        "type": "result",
                        "total_cost_usd": 0.42,
                        "num_turns": 3,
                        "session_id": "sess-001",
                        "is_error": False,
                    }
                )
                + "\n",
            ]
        )
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)

    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=7,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert res.is_error is False
    assert res.session_id == "sess-001"
    assert res.cost_usd == pytest.approx(0.42)
    assert res.turns == 3
    assert res.last_assistant_text == "hello"
    assert res.modified_files == ("src/edx/foo.py",)
    assert res.stream_path.exists()
    # All 3 events written verbatim.
    contents = res.stream_path.read_text(encoding="utf-8").splitlines()
    assert len(contents) == 3


def test_run_agent_terminates_on_budget(monkeypatch, tmp_path: Path) -> None:
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(
        cr, "_collect_modified_files", lambda *_a, **_kw: ()
    )

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        proc = _FakePopen(argv, **kwargs)
        proc.feed(
            [
                json.dumps({"type": "result", "total_cost_usd": 5.0})
                + "\n",
                json.dumps({"type": "result", "total_cost_usd": 9.0})
                + "\n",
            ]
        )
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)

    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=1,
        project_root=tmp_path,
        budget_usd=2.0,
        claude_executable=str(fake),
    )
    assert res.is_error is True
    assert "budget cap exceeded" in (res.error_summary or "")
    proc = _FakePopen.instances[-1]
    assert proc.terminated is True


def test_run_agent_terminates_on_max_turns(monkeypatch, tmp_path: Path) -> None:
    """The wrapper's turn guard is `max_turns + 5` (slack so claude's own
    --max-turns enforcement fires first and emits a clean result event).
    With max_turns=2 the wrapper guard is 7; feed 9 unique-id assistant
    messages so we cross it and assert the guard SIGTERM's the process."""
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(
        cr, "_collect_modified_files", lambda *_a, **_kw: ()
    )

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        proc = _FakePopen(argv, **kwargs)
        proc.feed(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"id": f"msg_{i:02d}", "content": []},
                    }
                )
                + "\n"
                for i in range(9)
            ]
        )
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)

    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=2,
        project_root=tmp_path,
        max_turns=2,
        claude_executable=str(fake),
    )
    assert res.is_error is True
    assert "max_turns exceeded" in (res.error_summary or "")


def test_run_agent_default_max_turns_reads_env(monkeypatch, tmp_path: Path) -> None:
    """Operator can tune max-turns budget via EDX_EVOLVE_MAX_TURNS env var
    (loaded by systemd from /opt/edx/.env.evolve) without touching code.
    The default falls back to 1000 — effectively unbounded for the
    operator's Max subscription where USD cost is informational (billed
    via subscription quota, not API). Earlier 25 / 60 / 100 defaults
    were found to preempt real agent work on ticks #67-#71."""
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(cr, "_collect_modified_files", lambda *_a, **_kw: ())

    captured_argv: list[str] = []

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_argv.clear()
        captured_argv.extend(argv)
        proc = _FakePopen(argv, **kwargs)
        proc.feed([json.dumps({"type": "result", "is_error": False}) + "\n"])
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)

    # Default: env unset → 1000.
    monkeypatch.delenv(cr.MAX_TURNS_ENV_VAR, raising=False)
    cr.run_agent(
        bundle_dir=tmp_path / "b1",
        tick_id=1,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert captured_argv[captured_argv.index("--max-turns") + 1] == "1000"

    # Operator override: env=120 → 120.
    monkeypatch.setenv(cr.MAX_TURNS_ENV_VAR, "120")
    cr.run_agent(
        bundle_dir=tmp_path / "b2",
        tick_id=2,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert captured_argv[captured_argv.index("--max-turns") + 1] == "120"

    # Garbage env value → fall back to default 1000.
    monkeypatch.setenv(cr.MAX_TURNS_ENV_VAR, "not-an-int")
    cr.run_agent(
        bundle_dir=tmp_path / "b3",
        tick_id=3,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert captured_argv[captured_argv.index("--max-turns") + 1] == "1000"


def test_run_agent_counts_unique_message_ids(
    monkeypatch, tmp_path: Path
) -> None:
    """Anti-regression for production tick #70 over-counting bug. stream-json
    emits the same logical assistant message multiple times as content
    blocks accumulate (text -> tool_use -> text -> ...), so a naive
    ``turns += 1`` per event inflates the count 2-4x and the wrapper
    SIGTERM's claude before it can finish.

    Setup: 8 assistant stream events, but only 3 distinct ``message.id``
    values — that's 3 real model turns, well within max_turns=5. Wrapper
    must NOT preempt; the run finishes cleanly."""
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(cr, "_collect_modified_files", lambda *_a, **_kw: ())

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        proc = _FakePopen(argv, **kwargs)
        proc.feed(
            [
                json.dumps({"type": "assistant", "message": {"id": "msg_A"}})
                + "\n",
                json.dumps({"type": "assistant", "message": {"id": "msg_A"}})
                + "\n",
                json.dumps({"type": "assistant", "message": {"id": "msg_A"}})
                + "\n",
                json.dumps({"type": "assistant", "message": {"id": "msg_B"}})
                + "\n",
                json.dumps({"type": "assistant", "message": {"id": "msg_B"}})
                + "\n",
                json.dumps({"type": "assistant", "message": {"id": "msg_C"}})
                + "\n",
                json.dumps({"type": "assistant", "message": {"id": "msg_C"}})
                + "\n",
                json.dumps({"type": "assistant", "message": {"id": "msg_C"}})
                + "\n",
                json.dumps(
                    {
                        "type": "result",
                        "is_error": False,
                        "num_turns": 3,
                        "total_cost_usd": 0.5,
                    }
                )
                + "\n",
            ]
        )
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)
    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=70,
        project_root=tmp_path,
        max_turns=5,
        claude_executable=str(fake),
    )
    assert res.is_error is False, (
        f"wrapper preempted despite only 3 unique message_ids; "
        f"error_summary={res.error_summary!r}"
    )
    # Final turn count comes from result.num_turns (3), not stream events (8).
    assert res.turns == 3
    proc = _FakePopen.instances[-1]
    assert proc.terminated is False, "wrapper SIGTERM'd unnecessarily"


def test_run_agent_strips_anthropic_api_key_from_child_env(
    monkeypatch, tmp_path: Path
) -> None:
    """Anti-regression for production tick #56: systemd loads /opt/edx/.env
    (with ANTHROPIC_API_KEY for the pipeline) AND /opt/edx/.env.evolve
    (with CLAUDE_CODE_OAUTH_TOKEN for the agent). claude's auth
    precedence puts API key ABOVE OAuth token, so the wrong creds win
    and claude gets 403. Wrapper MUST strip ANTHROPIC_API_KEY/AUTH_TOKEN
    from the child env before spawning claude."""
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-PIPELINE-KEY")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer-token")
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(cr, "_collect_modified_files", lambda *_a, **_kw: ())

    captured_env: dict[str, str] = {}

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal captured_env
        captured_env = dict(kwargs.get("env") or {})
        proc = _FakePopen(argv, **kwargs)
        proc.feed([json.dumps({"type": "result", "total_cost_usd": 0.0}) + "\n"])
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)
    cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=56,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert "ANTHROPIC_API_KEY" not in captured_env, (
        "ANTHROPIC_API_KEY must be stripped from claude subprocess env"
    )
    assert "ANTHROPIC_AUTH_TOKEN" not in captured_env, (
        "ANTHROPIC_AUTH_TOKEN must be stripped from claude subprocess env"
    )
    # Token MUST stay so claude can authenticate via Max OAuth.
    assert captured_env.get(cr.TOKEN_ENV_VAR) == "test-token"


def test_run_agent_argv_includes_verbose(monkeypatch, tmp_path: Path) -> None:
    """Anti-regression: stream-json + --print needs --verbose, otherwise
    Claude Code refuses to start with exit=1. Caught in production pilot
    on tick #9. Don't remove --verbose from claude_runner.argv."""
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(cr, "_collect_modified_files", lambda *_a, **_kw: ())

    captured_argv: list[str] = []

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_argv.extend(argv)
        proc = _FakePopen(argv, **kwargs)
        proc.feed([json.dumps({"type": "result", "total_cost_usd": 0.0}) + "\n"])
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)
    cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=99,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert "--verbose" in captured_argv, (
        "argv must include --verbose when using --print + stream-json"
    )
    assert "stream-json" in captured_argv


def test_run_agent_classifies_403_as_auth_failed(
    monkeypatch, tmp_path: Path
) -> None:
    """Anti-regression for production tick #61: when claude reaches Anthropic
    but the API rejects with 403 (typical cause on this VPS: missing
    HTTPS_PROXY in the systemd unit's child env), the wrapper must
    surface a precise ``auth_failed_403`` error_summary instead of the
    generic ``claude_run_error`` so the operator sees the real signal in
    ``edx evolve status`` without grepping claude.jsonl."""
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(cr, "_collect_modified_files", lambda *_a, **_kw: ())

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        proc = _FakePopen(argv, **kwargs)
        proc.feed(
            [
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": True,
                        "api_error_status": 403,
                        "num_turns": 1,
                        "total_cost_usd": 0,
                        "result": (
                            'Failed to authenticate. API Error: 403 '
                            '{"error":{"type":"forbidden",'
                            '"message":"Request not allowed"}}'
                        ),
                    }
                )
                + "\n",
            ]
        )
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)
    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=61,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert res.is_error is True
    assert res.error_summary == "auth_failed_403", (
        f"expected auth_failed_403, got: {res.error_summary!r}"
    )


def test_run_agent_skips_malformed_lines(monkeypatch, tmp_path: Path) -> None:
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(
        cr, "_collect_modified_files", lambda *_a, **_kw: ()
    )

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        proc = _FakePopen(argv, **kwargs)
        proc.feed(
            [
                "not-valid-json\n",
                "\n",
                json.dumps({"type": "result", "total_cost_usd": 0.1}) + "\n",
            ]
        )
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)

    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=3,
        project_root=tmp_path,
        claude_executable=str(fake),
    )
    assert res.is_error is False
    assert res.cost_usd == pytest.approx(0.1)
