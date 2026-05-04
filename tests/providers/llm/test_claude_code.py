"""ClaudeCodeLLMProvider tests with subprocess fully mocked.

The real ``claude`` CLI is never spawned; we patch
``asyncio.create_subprocess_exec`` and feed canned stream-json bytes,
asserting the provider parses them and constructs the right argv +
child env.

Anti-regression for the operator's hard-won lessons in
``evolution/MEMORY.md``: argv must include --verbose,
--permission-mode bypassPermissions, --dangerously-skip-permissions;
ANTHROPIC_API_KEY must be stripped from child env;
CLAUDE_CODE_OAUTH_TOKEN must be preserved.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from edx.providers.llm.base import LLMUnavailableError
from edx.providers.llm.claude_code_provider import (
    CLAUDE_OAUTH_ENV_VAR,
    ClaudeCodeLLMProvider,
)


class _FakeProc:
    """Minimal stand-in for the asyncio subprocess returned by
    ``create_subprocess_exec``. ``communicate()`` returns canned bytes."""

    def __init__(
        self,
        stdout: bytes,
        stderr: bytes = b"",
        returncode: int = 0,
        *,
        delay: float = 0.0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode: int | None = returncode
        self._delay = delay
        self.terminated = False
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _stream_json(
    *,
    text: str = '{"metric": "revenue", "value": 12345.0}',
    msg_id: str = "msg_001",
    in_tokens: int = 100,
    out_tokens: int = 20,
    is_error: bool = False,
    extra_assistant_chunks: list[str] | None = None,
) -> bytes:
    """Build a small stream-json bytestream that the provider can parse."""
    lines: list[str] = []
    lines.append(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess",
                "apiKeySource": "none",
            }
        )
    )
    base_msg = {"id": msg_id, "model": "claude-sonnet-4-6"}
    chunks = list(extra_assistant_chunks or []) + [text]
    for chunk in chunks:
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {**base_msg, "content": [{"type": "text", "text": chunk}]},
                }
            )
        )
    lines.append(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": is_error,
                "result": text if not is_error else "boom",
                "usage": {
                    "input_tokens": in_tokens,
                    "output_tokens": out_tokens,
                },
                "session_id": "sess",
            }
        )
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _ensure_oauth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider construction requires CLAUDE_CODE_OAUTH_TOKEN. Tests
    that want the missing-token path can monkeypatch.delenv() it back
    out."""
    monkeypatch.setenv(CLAUDE_OAUTH_ENV_VAR, "sk-ant-oat01-fake-test-token")


def _patch_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    fakes: list[_FakeProc],
    captured: dict[str, object] | None = None,
) -> None:
    """Replace asyncio.create_subprocess_exec with a stub that pops one
    canned _FakeProc per call and stores the argv/env into ``captured``."""
    fakes_iter = iter(fakes)

    async def _create(*argv: str, **kwargs: object) -> _FakeProc:
        if captured is not None:
            captured.setdefault("calls", []).append(  # type: ignore[union-attr]
                {"argv": list(argv), "env": kwargs.get("env")}
            )
        try:
            return next(fakes_iter)
        except StopIteration:
            raise AssertionError(
                "test exhausted its canned subprocess responses"
            ) from None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create)


# ----------------------------------------------------------- happy path

@pytest.mark.asyncio
async def test_happy_path_returns_parsed_json(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    captured: dict[str, object] = {}
    _patch_subprocess(
        monkeypatch,
        [_FakeProc(_stream_json())],
        captured,
    )
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    res = await provider.complete(request_factory())  # type: ignore[arg-type]
    assert res.provider == "claude_code"
    assert res.data == {"metric": "revenue", "value": 12345.0}
    assert res.input_tokens == 100
    assert res.output_tokens == 20


# --------------------------------------------------------- argv / env

@pytest.mark.asyncio
async def test_argv_contains_required_flags(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    """Anti-regression for evolve loop's hard lessons (a9c224f, 70df653,
    7451e44): --verbose required for -p+stream-json,
    bypassPermissions + dangerously-skip needed since there's no human."""
    captured: dict[str, object] = {}
    _patch_subprocess(
        monkeypatch,
        [_FakeProc(_stream_json())],
        captured,
    )
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    await provider.complete(request_factory())  # type: ignore[arg-type]

    argv: list[str] = captured["calls"][0]["argv"]  # type: ignore[index]
    assert argv[0] == "/usr/bin/claude"
    assert "-p" in argv
    assert "--output-format" in argv and "stream-json" in argv
    assert "--verbose" in argv
    assert "--permission-mode" in argv
    pm_idx = argv.index("--permission-mode")
    assert argv[pm_idx + 1] == "bypassPermissions"
    assert "--dangerously-skip-permissions" in argv
    assert "--max-turns" in argv
    assert "--model" in argv
    assert "--append-system-prompt" in argv


@pytest.mark.asyncio
async def test_strips_anthropic_api_key_from_child_env(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    """Anti-regression for evolve tick #56: ANTHROPIC_API_KEY in env
    hijacks the OAuth flow. Provider MUST strip both ANTHROPIC_*
    vars from the child env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-PIPELINE")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer-other")
    captured: dict[str, object] = {}
    _patch_subprocess(
        monkeypatch,
        [_FakeProc(_stream_json())],
        captured,
    )
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    await provider.complete(request_factory())  # type: ignore[arg-type]

    env: dict[str, str] = captured["calls"][0]["env"]  # type: ignore[index]
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    # OAuth token MUST stay so claude can authenticate via Max.
    assert env.get(CLAUDE_OAUTH_ENV_VAR) == "sk-ant-oat01-fake-test-token"


@pytest.mark.asyncio
async def test_create_raises_when_oauth_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CLAUDE_OAUTH_ENV_VAR, raising=False)
    with pytest.raises(LLMUnavailableError, match=CLAUDE_OAUTH_ENV_VAR):
        ClaudeCodeLLMProvider.create()


# ---------------------------------------------------------- PDF input

@pytest.mark.asyncio
async def test_pdf_input_staged_to_tempdir_and_add_dir(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    """req.pdf_bytes is written into a fresh temp dir, --add-dir points
    at it, the user prompt names the file path."""
    captured: dict[str, object] = {}
    _patch_subprocess(
        monkeypatch,
        [_FakeProc(_stream_json())],
        captured,
    )
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    await provider.complete(  # type: ignore[arg-type]
        request_factory(pdf_bytes=b"%PDF-1.4 fake")
    )
    argv: list[str] = captured["calls"][0]["argv"]  # type: ignore[index]
    assert "--add-dir" in argv
    add_dir = Path(argv[argv.index("--add-dir") + 1])
    # Tempdir is cleaned up by the time we get here, so the directory
    # itself shouldn't exist anymore — anti-regression for the
    # "leak temp PDFs" failure mode in MEMORY.md hard constraints.
    assert not add_dir.exists()
    # The user prompt must reference document.pdf so the model knows
    # which file to Read.
    prompt_idx = argv.index("-p")
    assert "document.pdf" in argv[prompt_idx + 1]


# ------------------------------------------------ JSON parsing edges

@pytest.mark.asyncio
async def test_json_code_fence_stripped(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    fenced = (
        "Here is your JSON:\n"
        "```json\n"
        '{"metric": "ebitda", "value": 999.0}\n'
        "```"
    )
    _patch_subprocess(monkeypatch, [_FakeProc(_stream_json(text=fenced))])
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    res = await provider.complete(request_factory())  # type: ignore[arg-type]
    assert res.data == {"metric": "ebitda", "value": 999.0}


@pytest.mark.asyncio
async def test_repair_retry_on_malformed_first_response(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    """First attempt: free-text without JSON. Second attempt: valid JSON.
    Provider must invoke claude twice and return the second's payload."""
    bad = _stream_json(text="I cannot extract any metrics here, sorry.")
    good = _stream_json(text='{"metric": "net_income", "value": 42.0}', msg_id="msg_002")
    captured: dict[str, object] = {}
    _patch_subprocess(monkeypatch, [_FakeProc(bad), _FakeProc(good)], captured)
    provider = ClaudeCodeLLMProvider.create(
        claude_executable="/usr/bin/claude", max_repair_attempts=1
    )
    res = await provider.complete(request_factory())  # type: ignore[arg-type]
    assert res.data == {"metric": "net_income", "value": 42.0}
    assert len(captured["calls"]) == 2  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_raises_when_json_unrecoverable(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    bad = _stream_json(text="no json here")
    _patch_subprocess(monkeypatch, [_FakeProc(bad), _FakeProc(bad)])
    provider = ClaudeCodeLLMProvider.create(
        claude_executable="/usr/bin/claude", max_repair_attempts=1
    )
    with pytest.raises(LLMUnavailableError, match="could not parse JSON"):
        await provider.complete(request_factory())  # type: ignore[arg-type]


# --------------------------------------------------- subprocess errors

@pytest.mark.asyncio
async def test_subprocess_returncode_nonzero_raises(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    _patch_subprocess(
        monkeypatch,
        [_FakeProc(b"", stderr=b"claude crashed", returncode=1)],
    )
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    with pytest.raises(LLMUnavailableError, match="subprocess error"):
        await provider.complete(request_factory())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_result_event_is_error_raises(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    """If claude emits result.is_error=True (e.g. auth_failed_403), the
    provider raises LLMUnavailableError. No silent retry."""
    _patch_subprocess(
        monkeypatch,
        [_FakeProc(_stream_json(is_error=True))],
    )
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    with pytest.raises(LLMUnavailableError, match="subprocess error"):
        await provider.complete(request_factory())  # type: ignore[arg-type]


# --------------------------------------------------------- concurrency

@pytest.mark.asyncio
async def test_concurrent_calls_get_distinct_tempdirs(
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[..., object],
) -> None:
    """Three concurrent calls must each get a fresh tempdir. Anti-
    regression for the cross-call PDF clobber failure mode."""
    captured: dict[str, object] = {"calls": []}

    async def _create(*argv: str, **kwargs: object) -> _FakeProc:
        captured["calls"].append(  # type: ignore[union-attr]
            {"argv": list(argv), "env": kwargs.get("env")}
        )
        return _FakeProc(_stream_json())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create)
    provider = ClaudeCodeLLMProvider.create(claude_executable="/usr/bin/claude")
    requests = [
        request_factory(pdf_bytes=f"%PDF-{i}".encode())  # type: ignore[arg-type]
        for i in range(3)
    ]
    await asyncio.gather(*[provider.complete(r) for r in requests])

    add_dirs: list[str] = []
    for call in captured["calls"]:  # type: ignore[arg-type]
        argv = call["argv"]
        if "--add-dir" in argv:
            add_dirs.append(argv[argv.index("--add-dir") + 1])
    assert len(set(add_dirs)) == len(add_dirs) == 3
