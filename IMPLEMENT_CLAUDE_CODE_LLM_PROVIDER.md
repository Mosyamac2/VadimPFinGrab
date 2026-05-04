# Implement `ClaudeCodeLLMProvider` — route pipeline LLM calls through `claude -p`

> **Audience:** an in-loop Claude Code agent (or operator running `claude`
> interactively) that picks this file up via `--add-dir` or `cat | -p` and
> executes it end-to-end.
>
> **Goal:** the e-disclosure ETL pipeline's metric extractor and any future
> LLM-using stage routes 100% of its calls through subprocess
> `claude -p ...`, billed against the operator's Anthropic Max
> subscription via `CLAUDE_CODE_OAUTH_TOKEN`. After this lands, the
> operator can revoke `ANTHROPIC_API_KEY` and the loop keeps producing
> metrics for free under Max quota.
>
> **Why subprocess:** Anthropic's API server returns
> `401 OAuth authentication is currently not supported` when the
> `anthropic` Python SDK is invoked with `auth_token=` carrying a Max
> OAuth token. The `claude` CLI binary is the *only* sanctioned channel
> for OAuth-billed completions. We must spawn it as a subprocess and
> parse stream-json. There is no SDK shortcut.

---

## 0. Read these files first (STEP 0)

Before any edits:

1. `evolution/MEMORY.md` — anti-patterns the loop has learned (proxy,
   --verbose, env-strip, turn counting, taxonomy gates, etc.). Don't
   reintroduce any.
2. `src/edx/evolve/claude_runner.py` — the existing `claude -p`
   invocation. Reuse the same auth-strip + proxy-passthrough hygiene.
3. `src/edx/providers/llm/anthropic_provider.py` — the contract the
   new provider must match (input → `LLMResponse`).
4. `src/edx/providers/llm/base.py` — the `LLMProvider` Protocol,
   `LLMRequest`, `LLMResponse`, `LLMUnavailableError`.
5. `src/edx/providers/llm/factory.py` — where the new provider slots in.
6. `src/edx/providers/llm/cache.py` — `CachedLLMProvider` wraps the
   inner; verify its key derivation works for our content.
7. `src/edx/stages/metric_extractor/service.py` — the heaviest
   consumer; understand its prompts and expected JSON shape.
8. `config/llm.yaml` — current configuration knobs.
9. `config/metrics.yaml` — schemas the metric extractor sends; the
   JSON shapes the model must emit.
10. `tests/providers/llm/test_anthropic.py` — the test pattern to mirror.

## 1. Architectural decisions (don't reopen these)

- **Subprocess per call**, not a long-lived `claude` session. Each
  metric/event extraction LLM call spawns its own `claude -p`. Reasons:
  - Each call has a different prompt and PDF; sessions can't easily be
    reused.
  - Subprocess crash isolation: a hung claude on one document doesn't
    block the next.
  - Concurrency = N parallel subprocesses, mirrors the in-process
    concurrency the API client already supports.
- **Parsing JSON from free-form assistant text**, not tool-use. CLI
  doesn't expose tool definitions to `-p` callers in a way that lets us
  drop our own tool. Pattern: tell the model "your response MUST be
  one JSON object matching this schema, no preamble, no code fences" in
  the system prompt; parse the assistant's last text block; on malformed
  JSON do **one** repair retry with the original error pasted back.
- **PDF input via `--add-dir` + Read-tool prompt**. Write `req.pdf_bytes`
  to a temp file, pass that file's directory via `--add-dir`, and tell
  the model in the user message to read it with the Read tool. The
  Read tool surfaces PDF content as multimodal blocks the model can see,
  same as native `document` blocks in the API.
  - For the `pdf_page_images` path (vision-only), do the same with PNG
    files. Read tool understands images.
  - For the `pdf_page_indices` path (PDF slicing), keep the existing
    `_slice_pdf_pages` helper from `anthropic_provider.py` and slice
    before writing to the temp file.
- **No prompt caching** (CLI manages it internally; we don't get
  per-block `cache_control`). Accept higher token billing on Max but
  it's free for the operator anyway.
- **Token accounting**: parse `result.usage` from stream-json. Set
  `cache_read_input_tokens` and `cache_creation_input_tokens` to 0 in
  `LLMResponse` when CLI doesn't surface them — it's informational.

## 2. New file: `src/edx/providers/llm/claude_code_provider.py`

Skeleton:

```python
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
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from edx.logging_setup import get_logger
from edx.providers.llm.base import (
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)


CLAUDE_OAUTH_ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
DEFAULT_TIMEOUT_S = 300  # one LLM call ceiling


class ClaudeCodeLLMProvider:
    """Spawn ``claude -p`` per call. Output is stream-json; parse the
    final ``result`` event for the assistant text + usage."""

    name = "claude_code"
    supports_pdf_input = True

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        timeout_seconds: int = DEFAULT_TIMEOUT_S,
        claude_executable: str | None = None,
        max_repair_attempts: int = 1,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.claude_executable = (
            claude_executable or shutil.which("claude")
        )
        self.max_repair_attempts = max_repair_attempts
        self._log = get_logger("edx.providers.llm.claude_code")

    @classmethod
    def create(cls, **kwargs: Any) -> "ClaudeCodeLLMProvider":
        if not os.environ.get(CLAUDE_OAUTH_ENV_VAR):
            raise LLMUnavailableError(
                f"{CLAUDE_OAUTH_ENV_VAR} not set; ClaudeCodeLLMProvider "
                f"needs a Max OAuth token (claude setup-token)."
            )
        return cls(**kwargs)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        # 1. Build prompt (system + user_text + JSON-only instruction).
        # 2. If req has PDF/images, write to a tempdir + --add-dir it,
        #    and reference the file by path in the user message.
        # 3. Spawn subprocess, read stream-json, capture last text +
        #    usage.
        # 4. Try to parse JSON from the text; on failure, do one
        #    repair attempt with the parse error fed back.
        # 5. Return LLMResponse.
        ...
```

Key methods to implement (in order):

- `_build_prompt(req: LLMRequest) -> tuple[str, str, list[Path]]`
  Returns `(system_prompt, user_prompt, temp_dirs_to_clean_up)`. The
  user prompt embeds explicit file paths the model should Read.

- `_run_claude(system: str, user: str, add_dir: Path | None) -> tuple[str, dict]`
  Async subprocess invocation. Argv (build it as a list, never a
  string):
  ```
  [claude_bin, "-p", user_prompt,
   "--output-format", "stream-json",
   "--verbose",                      # required pair with -p+stream-json
   "--max-turns", "5",                # JSON extraction is fast
   "--permission-mode", "bypassPermissions",
   "--dangerously-skip-permissions",
   "--append-system-prompt", system,  # if available, else system to prompt
   "--model", model,
   "--add-dir", str(add_dir),         # only if add_dir
  ]
  ```
  Run via `asyncio.create_subprocess_exec`, read stdout line-by-line,
  parse stream-json events. Capture:
  - The last assistant text block (the JSON answer).
  - The `result` event's `usage.input_tokens`, `usage.output_tokens`,
    `total_cost_usd`, `is_error`, `subtype`.
  Apply the same env-strip as `claude_runner.run_agent`:
  ```python
  child_env = os.environ.copy()
  for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
      child_env.pop(k, None)
  ```

- `_parse_json(text: str, schema_name: str) -> dict`
  Extract the first ``{...}`` block from `text` (handle common
  failure modes: ```` ```json fences ````, leading prose, trailing
  prose). On `json.JSONDecodeError`, raise `LLMUnavailableError`
  with the offending substring so `_attempt_with_repair` can retry.

- `_validate_against_schema(data: dict, schema: dict) -> None`
  Use `jsonschema.validate` (already a transitive dep) and on
  mismatch raise the same error type — repair attempt will see it.

- `_attempt_with_repair(req: LLMRequest) -> LLMResponse`
  Try once; on parse/validate failure, retry with a follow-up user
  message that includes the original error and tells the model to
  emit corrected JSON only. If still bad, raise
  `LLMUnavailableError`.

## 3. Wiring into the factory

In `src/edx/providers/llm/factory.py`:

- Read a new env-var `EDX_LLM_PROVIDER` (with a sane default).
  - `EDX_LLM_PROVIDER=claude_code` → instantiate `ClaudeCodeLLMProvider`.
  - `EDX_LLM_PROVIDER=anthropic` (or unset) → keep current behaviour.
  - Anything else → raise with a helpful list.
- Auto-pick: if `CLAUDE_CODE_OAUTH_TOKEN` is set AND
  `ANTHROPIC_API_KEY` is missing, default to `claude_code`. If both
  are set, prefer `anthropic` (faster, more reliable JSON), let the
  operator opt into Claude Code via env. Document this in the
  docstring.
- The cache wrapper logic stays the same.

## 4. Stages that produce LLM requests

These need no changes — they call `LLMProvider.complete(req)` and the
factory now returns the right provider:

- `src/edx/stages/metric_extractor/service.py`
- `src/edx/stages/event_extractor/service.py` (if present)
- Any other consumer of `LLMRequest`

…**unless** their `LLMRequest.pdf_bytes` or `pdf_page_images` produces
content the new provider can't render. Audit:

- `metric_extractor` paths to `LLMRequest`: which docs go via
  `pdf_bytes` vs `pdf_page_images` vs text-only?
- For text-only requests there's nothing special to do.
- For PDF/image requests, verify the new provider's tempfile +
  Read-tool path produces JSON of acceptable quality on at least one
  representative document. If quality is poor, document the gap as a
  known limitation in `evolution/MEMORY.md`.

## 5. Configuration

`config/llm.yaml` updates:

- Add a new top-level key `claude_code:` mirroring `primary:`:
  ```yaml
  claude_code:
    enabled: true
    model: claude-sonnet-4-6
    timeout_seconds: 300
    max_repair_attempts: 1
  ```
- Existing `primary:` (Anthropic API) and `fallback:` (OpenRouter)
  blocks stay; the factory chooses.

`src/edx/config/llm_config.py` — add the matching dataclass / pydantic
model field.

## 6. Tests (`tests/providers/llm/test_claude_code.py`)

Mock `subprocess.create_subprocess_exec` (or asyncio analogue) to feed
canned stream-json bytes. Cover:

1. **Happy path**: provider receives a `LLMRequest`, spawns claude
   with the right argv, parses JSON, returns `LLMResponse` with
   correct token counts.
2. **Argv flags**: assert `--verbose`, `--permission-mode
   bypassPermissions`, `--dangerously-skip-permissions`,
   `--max-turns`, `--model` are all in argv. Anti-regression for the
   evolve loop's hard-won lessons (commits a9c224f, 70df653, 7451e44).
3. **ANTHROPIC_API_KEY stripped from child env**, same as evolve's
   `test_run_agent_strips_anthropic_api_key_from_child_env`.
4. **CLAUDE_CODE_OAUTH_TOKEN preserved** in child env.
5. **PDF input**: when `req.pdf_bytes` is set, the temp file is
   created, `--add-dir` points to its parent, the user prompt
   references the file path. The temp dir is cleaned up after the
   call (use `tempfile.TemporaryDirectory()` context manager).
6. **JSON code-fence stripped**: assistant emits ```` ```json {...} ```` ````,
   provider extracts the inner object.
7. **Repair retry on malformed JSON**: first response is invalid JSON,
   second is valid. Provider returns the second's parsed payload.
8. **Schema validation**: response valid JSON but missing required
   field → repair retry → still wrong → raise `LLMUnavailableError`.
9. **Subprocess timeout**: spawn returns slowly past
   `timeout_seconds`. Provider SIGTERMs and raises
   `LLMUnavailableError("claude_code timeout")`.
10. **Concurrent calls don't cross temp dirs**: spawn 3 in parallel
    via `asyncio.gather`, each gets its own tempdir, no clobber.
11. **Sentinel for env-var fast-fail**: `CLAUDE_CODE_OAUTH_TOKEN`
    missing → `LLMUnavailableError` from `create()`.

Plus update `tests/providers/llm/test_factory.py`:

- New test: `EDX_LLM_PROVIDER=claude_code` with
  `CLAUDE_CODE_OAUTH_TOKEN` set returns
  `CachedLLMProvider(ClaudeCodeLLMProvider)`.
- New test: `EDX_LLM_PROVIDER=claude_code` without
  `CLAUDE_CODE_OAUTH_TOKEN` raises.
- New test: auto-pick behaviour (no `EDX_LLM_PROVIDER`,
  `ANTHROPIC_API_KEY` missing, `CLAUDE_CODE_OAUTH_TOKEN` set →
  ClaudeCode).

## 7. Validation against a real document

After `make test` passes, do an integration smoke:

1. Pick one already-extracted publication from
   `data/processed/EDX1021/` (NOTA-Bank, has 55 metrics in DB so
   the doc is known-extractable).
2. Run the metric extractor on it directly with
   `EDX_LLM_PROVIDER=claude_code`:
   ```
   .venv/bin/python -c "
   import asyncio
   from edx.config import load_all
   from edx.providers.llm.factory import build_llm_provider
   from edx.stages.metric_extractor.service import MetricExtractorService
   ...
   "
   ```
3. Compare extracted metrics against what's in `metrics` table for
   that publication. Acceptable tolerance: ±20% of metric count
   (some may be missing due to JSON parse losses); no hallucinated
   metrics.

If smoke fails, dig into the assistant text (`stream_path` artifact)
and tighten the system prompt's JSON-only instruction.

## 8. Hard constraints (do not violate)

- `CLAUDE_CODE_OAUTH_TOKEN` from `.env.evolve` MUST reach the child
  process. If not present in env, fail loudly at provider construction
  (don't silently fall through).
- `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` MUST be stripped from
  the child env before spawning claude (per evolve loop's MEMORY.md
  anti-pattern — these env vars take precedence over OAuth in the CLI
  and break auth).
- `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` MUST be passed through to the
  child env (per same anti-pattern — Russian VPS needs proxy for
  api.anthropic.com).
- Each subprocess gets its own temp dir; clean up via
  `tempfile.TemporaryDirectory()` so we don't leak PDFs.
- Don't write secrets to logs. Log token counts and model only.
- Don't break the existing `AnthropicLLMProvider` path. Operators with
  API credits should keep working unchanged when
  `EDX_LLM_PROVIDER=anthropic` (or unset and the API key is set).

## 9. STEP 4 — update `evolution/MEMORY.md`

Append to `## Anti-patterns`:

```
- **NEVER** call the ``anthropic`` SDK with ``auth_token=`` carrying a
  Max OAuth token expecting it to work — Anthropic returns 401
  "OAuth authentication is currently not supported" on /v1/messages.
  The CLI binary is the only sanctioned channel for OAuth-billed
  completions. **Why:** Anthropic separates Max plan from API plan
  at the server. **How to apply:** if you need to route pipeline LLM
  calls through Max, use ``ClaudeCodeLLMProvider`` (subprocess
  ``claude -p``) — never the SDK shortcut.
```

Append to `## Patches log`:

```
### evolve(N) — YYYY-MM-DD — pipeline_oauth_max_billing
- **Tick:** #N — single-ticker batch (irrelevant; this is a structural change)
- **Failing companies:** N/A (post-credit-exhaustion infrastructure work)
- **Root cause:** Anthropic API + OpenRouter both 402 (insufficient
  credits). Operator wanted to consolidate billing onto the Max
  subscription that already powers the evolve agent. The Anthropic
  Python SDK rejects OAuth tokens; the only path is subprocess
  ``claude -p``.
- **Files touched:**
  - src/edx/providers/llm/claude_code_provider.py (new)
  - src/edx/providers/llm/factory.py (router + env-var)
  - src/edx/providers/llm/__init__.py (export)
  - src/edx/config/llm_config.py (claude_code config block)
  - config/llm.yaml (claude_code defaults)
- **Tests added:** tests/providers/llm/test_claude_code.py (12 tests),
  test_factory.py (3 new env-routing tests)
- **Anti-regression notes:**
  - DO NOT silently fall through to AnthropicLLMProvider when
    EDX_LLM_PROVIDER=claude_code is requested but OAUTH token missing
    — fail loudly, this is an operator-config error.
  - DO NOT remove the ANTHROPIC_API_KEY env strip in the new
    provider's child env — same precedence bug as the evolve agent.
  - DO NOT cache the temp PDF path between calls — each call gets a
    fresh tempdir cleaned up via context manager.
- **Coverage delta on batch:** N/A
```

## 10. Done criteria

The PR (or working-tree edits in this tick) is complete when:

- [ ] `tests/providers/llm/test_claude_code.py` exists with ≥10 tests, all green.
- [ ] `tests/providers/llm/test_factory.py` has 3 new tests, all green.
- [ ] `make lint`, `make typecheck`, `make test` all green.
- [ ] Smoke run on one EDX1021 publication produces ≥1 metric via
      `EDX_LLM_PROVIDER=claude_code`.
- [ ] `evolution/MEMORY.md` has the new anti-pattern + patch log entry.
- [ ] `SUMMARY.md` written for the tick (if running inside an evolve tick).
- [ ] No regression in existing `AnthropicLLMProvider` tests.

After landing, the operator can:

1. Set `EDX_LLM_PROVIDER=claude_code` in `/opt/edx/.env.evolve`.
2. Remove `ANTHROPIC_API_KEY` from `/opt/edx/.env` (optional —
   stripped from child env anyway).
3. Watch tick #N+1 process a previously-stuck company and produce
   metrics with $0 cost on the Anthropic Console dashboard.

## 11. Out of scope (future work, do NOT attempt now)

- Fine-grained prompt caching (CLI manages it; we don't get
  `cache_control` knobs).
- Streaming partial responses to consumers (metric extractor doesn't
  consume streams).
- Replacing the Anthropic provider entirely. Operators who pay for
  API credits keep that path; the new provider is *opt-in* via env var.
- Routing the canary check or any other non-LLM call through `claude`.
- Migrating `event_extractor` (if it exists and uses LLM) — same
  factory swap covers it automatically; just verify quality post-change.
