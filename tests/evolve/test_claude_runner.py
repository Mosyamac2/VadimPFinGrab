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
        stdout=None,
        stderr=None,
        text=False,
        bufsize=0,
    ):  # type: ignore[no-untyped-def]
        _FakePopen.instances.append(self)
        self.argv = list(argv)
        self.cwd = cwd
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
    fake = _enable_claude(monkeypatch, tmp_path)
    monkeypatch.setattr(cr, "_git_head", lambda _root: None)
    monkeypatch.setattr(
        cr, "_collect_modified_files", lambda *_a, **_kw: ()
    )

    def _factory(argv, **kwargs):  # type: ignore[no-untyped-def]
        proc = _FakePopen(argv, **kwargs)
        proc.feed(
            [json.dumps({"type": "assistant", "message": {"content": []}}) + "\n"]
            * 5
        )
        return proc

    monkeypatch.setattr(cr.subprocess, "Popen", _factory)

    res = cr.run_agent(
        bundle_dir=tmp_path / "bundle",
        tick_id=2,
        project_root=tmp_path,
        max_turns=3,
        claude_executable=str(fake),
    )
    assert res.is_error is True
    assert "max_turns exceeded" in (res.error_summary or "")


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
