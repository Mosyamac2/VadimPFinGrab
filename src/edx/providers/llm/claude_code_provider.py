"""Route LLM calls through subprocess ``claude -p`` for Max-OAuth billing.

Mirrors :class:`AnthropicLLMProvider`'s contract but spawns the
``claude`` CLI binary instead of using the ``anthropic`` SDK. Each
call is one short-lived subprocess; concurrency is achieved by N
parallel subprocesses (the existing in-process concurrency limit
in ``MetricExtractorService`` controls the cap).

Why subprocess (and not the ``anthropic`` SDK with ``auth_token=``):
the Anthropic API server returns 401 "OAuth authentication is
currently not supported" on /v1/messages when the SDK passes a Max
OAuth token. The ``claude`` CLI has private server-side support for
OAuth that the SDK does not.

Implementation contract (per /opt/edx/IMPLEMENT_CLAUDE_CODE_LLM_PROVIDER.md):

- Subprocess per call (no long-lived session).
- JSON parsed defensively from free-form assistant text. One repair
  retry on parse/validate failure with the error fed back to the model.
- PDF input goes via tempfile + ``--add-dir`` + Read-tool prompt.
- Same env hygiene as ``edx.evolve.claude_runner.run_agent``: strip
  ``ANTHROPIC_API_KEY``/``ANTHROPIC_AUTH_TOKEN`` from child env so
  the precedence-bug from MEMORY.md tick #56 doesn't recur. Proxy
  vars (``HTTPS_PROXY`` etc.) are inherited via ``os.environ.copy()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Final

from edx.logging_setup import get_logger
from edx.providers.llm.base import (
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)

CLAUDE_OAUTH_ENV_VAR: Final[str] = "CLAUDE_CODE_OAUTH_TOKEN"
DEFAULT_TIMEOUT_S: Final[int] = 300
DEFAULT_MAX_TURNS: Final[int] = 5
DEFAULT_MODEL: Final[str] = "claude-sonnet-4-6"

# Environment variables stripped from the child claude process. Same
# rationale as edx.evolve.claude_runner: claude's auth precedence puts
# ANTHROPIC_API_KEY ABOVE CLAUDE_CODE_OAUTH_TOKEN, and a stale
# pipeline API key can hijack OAuth-billed calls and 403.
_ANTHROPIC_AUTH_VARS: Final[tuple[str, ...]] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)

# JSON code-fence patterns the model commonly produces. We strip them
# before json.loads so a "```json {...} ```" wrapper doesn't fail us.
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ClaudeCodeLLMProvider:
    """Spawn ``claude -p`` per call. Output is stream-json; parse the
    final ``result`` event for the assistant text + usage."""

    name = "claude_code"
    supports_pdf_input = True

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_S,
        max_turns: int = DEFAULT_MAX_TURNS,
        claude_executable: str | None = None,
        max_repair_attempts: int = 1,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns
        self.claude_executable = (
            claude_executable or shutil.which("claude") or "claude"
        )
        self.max_repair_attempts = max_repair_attempts
        self._log = get_logger("edx.providers.llm.claude_code")

    @classmethod
    def create(
        cls,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_S,
        max_turns: int = DEFAULT_MAX_TURNS,
        claude_executable: str | None = None,
        max_repair_attempts: int = 1,
    ) -> ClaudeCodeLLMProvider:
        if not os.environ.get(CLAUDE_OAUTH_ENV_VAR):
            raise LLMUnavailableError(
                f"{CLAUDE_OAUTH_ENV_VAR} not set; ClaudeCodeLLMProvider "
                f"needs a Max OAuth token (run `claude setup-token` and "
                f"paste the result into /opt/edx/.env.evolve)."
            )
        return cls(
            model=model,
            timeout_seconds=timeout_seconds,
            max_turns=max_turns,
            claude_executable=claude_executable,
            max_repair_attempts=max_repair_attempts,
        )

    async def complete(self, req: LLMRequest) -> LLMResponse:
        with tempfile.TemporaryDirectory(prefix="edx_claude_code_") as tmp:
            tmpdir = Path(tmp)
            attachments = self._stage_attachments(req, tmpdir)
            system = self._build_system_prompt(req)
            user = self._build_user_prompt(req, attachments)
            return await self._attempt_with_repair(
                req=req,
                system=system,
                user=user,
                add_dir=tmpdir if attachments else None,
            )

    # ------------------------------------------------------------ internals

    async def _attempt_with_repair(
        self,
        *,
        req: LLMRequest,
        system: str,
        user: str,
        add_dir: Path | None,
    ) -> LLMResponse:
        """Run claude once; on JSON parse/validate failure, retry once
        with the error injected as a follow-up nudge."""
        last_error: str | None = None
        last_usage: dict[str, int] = {"input": 0, "output": 0}
        for attempt in range(self.max_repair_attempts + 1):
            user_for_attempt = user
            if attempt > 0 and last_error is not None:
                user_for_attempt = (
                    f"{user}\n\nYour previous response failed to parse "
                    f"as the required JSON: {last_error[:500]}\n"
                    f"Re-emit ONLY the corrected JSON object — no prose, "
                    f"no markdown fences, no commentary."
                )
            text, usage = await self._run_claude(
                system=system, user=user_for_attempt, add_dir=add_dir
            )
            last_usage = usage
            try:
                data = self._parse_json(text)
            except LLMUnavailableError as exc:
                last_error = str(exc)
                continue
            return LLMResponse(
                data=data,
                raw_text=text,
                provider=self.name,
                model=self.model,
                input_tokens=usage["input"],
                output_tokens=usage["output"],
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            )
        # Exhausted repair attempts.
        self._log.warning(
            "llm_request_completed",
            provider=self.name,
            model=self.model,
            status="failed",
            error=last_error,
            input_tokens=last_usage["input"],
            output_tokens=last_usage["output"],
        )
        raise LLMUnavailableError(
            f"claude_code: could not parse JSON after "
            f"{self.max_repair_attempts + 1} attempts: {last_error}"
        )

    async def _run_claude(
        self, *, system: str, user: str, add_dir: Path | None
    ) -> tuple[str, dict[str, int]]:
        """Spawn ``claude -p`` once; return (assistant_text, usage_dict).

        Raises :class:`LLMUnavailableError` on subprocess timeout, claude
        exit error, or stream-json result with ``is_error=true``.
        """
        # Pass the user prompt via stdin, not as a positional CLI arg.
        # Linux MAX_ARG_STRLEN (128 KB) limits individual argv strings;
        # large RSBU documents assembled from 100+ pages can exceed it,
        # causing OSError [Errno 7]. `claude -p` reads from stdin when no
        # positional [prompt] argument is given ("useful for pipes").
        argv: list[str] = [
            self.claude_executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(self.max_turns),
            "--permission-mode",
            "bypassPermissions",
            "--dangerously-skip-permissions",
            "--model",
            self.model,
            "--append-system-prompt",
            system,
        ]
        if add_dir is not None:
            argv.extend(["--add-dir", str(add_dir.resolve())])

        # Strip Anthropic API auth vars so claude falls through to
        # CLAUDE_CODE_OAUTH_TOKEN. Same as edx.evolve.claude_runner.
        child_env = os.environ.copy()
        for key in _ANTHROPIC_AUTH_VARS:
            child_env.pop(key, None)

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
        except (OSError, FileNotFoundError) as exc:
            raise LLMUnavailableError(
                f"claude_code: cannot spawn {self.claude_executable!r}: {exc}"
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=user.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            raise LLMUnavailableError(
                f"claude_code: timed out after {self.timeout_seconds}s"
            ) from exc

        elapsed = time.monotonic() - t0
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        last_text, usage, is_error, error_msg = _parse_stream_json(stdout_text)
        self._log.info(
            "llm_request_completed",
            provider=self.name,
            model=self.model,
            input_tokens=usage["input"],
            output_tokens=usage["output"],
            elapsed_s=round(elapsed, 4),
            status="failed" if is_error else "success",
        )
        if is_error or proc.returncode not in (0, None):
            stderr_tail = stderr_bytes.decode("utf-8", errors="replace")[-500:]
            raise LLMUnavailableError(
                f"claude_code: subprocess error "
                f"(returncode={proc.returncode}, "
                f"is_error={is_error}, "
                f"msg={error_msg or stderr_tail or 'unknown'})"
            )
        return last_text, usage

    def _build_system_prompt(self, req: LLMRequest) -> str:
        """Wrap the caller's system prompt with strict JSON-only
        instructions so we can parse the assistant text deterministically.
        """
        schema_blob = json.dumps(req.json_schema, ensure_ascii=False, indent=2)
        return (
            f"{req.system}\n\n"
            f"=== STRICT OUTPUT FORMAT (claude-code provider) ===\n"
            f"Your response MUST be exactly ONE JSON object that conforms "
            f"to the schema below. No markdown code fences. No prose "
            f"before or after. No commentary. The JSON object must be "
            f"the entire content of your final assistant message.\n\n"
            f"Schema (named '{req.schema_name}'):\n"
            f"```\n{schema_blob}\n```"
        )

    def _build_user_prompt(
        self, req: LLMRequest, attachments: list[Path]
    ) -> str:
        """Compose the user-message text. If we staged PDFs/images on
        disk, instruct the model to Read them. Otherwise the user text
        is sent verbatim."""
        if not attachments:
            return req.user_text
        bullets = "\n".join(
            f"- {path.name} (use the Read tool: Read('{path}'))"
            for path in attachments
        )
        return (
            f"{req.user_text}\n\n"
            f"=== ATTACHED FILES (use the Read tool to inspect) ===\n"
            f"{bullets}"
        )

    def _stage_attachments(
        self, req: LLMRequest, tmpdir: Path
    ) -> list[Path]:
        """Write any binary inputs to ``tmpdir`` and return their paths.
        Order: page images take precedence over pdf_bytes (matches
        AnthropicLLMProvider's behaviour)."""
        out: list[Path] = []
        if req.pdf_page_images:
            for idx, image_bytes in enumerate(req.pdf_page_images):
                p = tmpdir / f"page_{idx:03d}.png"
                p.write_bytes(image_bytes)
                out.append(p)
            return out
        if req.pdf_bytes is not None:
            payload = (
                _slice_pdf_pages(req.pdf_bytes, req.pdf_page_indices)
                if req.pdf_page_indices
                else req.pdf_bytes
            )
            p = tmpdir / "document.pdf"
            p.write_bytes(payload)
            out.append(p)
        return out

    def _parse_json(self, text: str) -> dict[str, Any]:
        """Extract the first JSON object from ``text``. Tolerates
        ```json fenced blocks and leading/trailing prose. Raises
        :class:`LLMUnavailableError` (caught by the repair loop) on
        decode failure.
        """
        candidate = text.strip()
        # Try a fenced block first.
        fence_match = _FENCE_RE.search(candidate)
        if fence_match:
            candidate = fence_match.group(1)
        else:
            # Locate the outermost {...} substring by bracket counting.
            extracted = _extract_outer_object(candidate)
            if extracted is not None:
                candidate = extracted
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError(
                f"claude_code: response is not valid JSON: {exc.msg} "
                f"at pos={exc.pos}; got: {candidate[:200]!r}"
            ) from exc
        if not isinstance(data, dict):
            raise LLMUnavailableError(
                f"claude_code: response parsed to {type(data).__name__}, "
                f"expected JSON object"
            )
        return data


def _parse_stream_json(
    raw: str,
) -> tuple[str, dict[str, int], bool, str | None]:
    """Walk every JSONL line in ``raw`` (claude stream-json output) and
    return:

    - ``last_text``: concatenated text blocks of the LAST assistant
      message_id seen (the model's final answer).
    - ``usage``: ``{"input": N, "output": N}`` from the ``result`` event.
    - ``is_error``: True if the result event has ``is_error=True``.
    - ``error_msg``: the result event's ``result`` field on error.
    """
    last_id: str | None = None
    texts_by_id: dict[str, list[str]] = {}
    usage: dict[str, int] = {"input": 0, "output": 0}
    is_error = False
    error_msg: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        ev_type = event.get("type")
        if ev_type == "assistant":
            msg = event.get("message")
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
            if isinstance(msg_id, str):
                last_id = msg_id
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                ):
                    text = block.get("text")
                    if isinstance(text, str) and msg_id is not None:
                        texts_by_id.setdefault(msg_id, []).append(text)
        elif ev_type == "result":
            if event.get("is_error") is True:
                is_error = True
            res = event.get("result")
            if isinstance(res, str):
                error_msg = res
            event_usage = event.get("usage")
            if isinstance(event_usage, dict):
                in_t = event_usage.get("input_tokens")
                out_t = event_usage.get("output_tokens")
                if isinstance(in_t, int):
                    usage["input"] = in_t
                if isinstance(out_t, int):
                    usage["output"] = out_t
    last_text = "".join(texts_by_id.get(last_id, [])) if last_id else ""
    return last_text, usage, is_error, error_msg


def _extract_outer_object(text: str) -> str | None:
    """Return the substring covering the first balanced ``{...}`` in
    ``text``, ignoring braces inside string literals. ``None`` if no
    balanced object is found."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _slice_pdf_pages(pdf_bytes: bytes, indices: tuple[int, ...]) -> bytes:
    """Return a new PDF containing only ``indices`` (0-based) from
    ``pdf_bytes``. Mirrors the helper in ``anthropic_provider`` so the
    two providers slice identically. Out-of-range indices are dropped.
    """
    import pymupdf

    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")  # type: ignore[no-untyped-call]
    dst = pymupdf.open()  # type: ignore[no-untyped-call]
    try:
        page_count = src.page_count
        for idx in indices:
            if 0 <= idx < page_count:
                dst.insert_pdf(src, from_page=idx, to_page=idx)  # type: ignore[no-untyped-call]
        out = io.BytesIO()
        dst.save(out)  # type: ignore[no-untyped-call]
        return out.getvalue()
    finally:
        dst.close()  # type: ignore[no-untyped-call]
        src.close()  # type: ignore[no-untyped-call]


__all__ = [
    "CLAUDE_OAUTH_ENV_VAR",
    "ClaudeCodeLLMProvider",
]
