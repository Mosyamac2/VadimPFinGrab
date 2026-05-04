"""Build the LLM provider from settings + secrets.

Two providers are wired in:

- ``AnthropicLLMProvider`` — direct Anthropic API via the SDK.
  Requires ``ANTHROPIC_API_KEY``. Fastest, supports tool-use for
  strict JSON, supports prompt caching with explicit TTL, supports
  base64 PDF/image content blocks. Pay-per-token billing.

- ``ClaudeCodeLLMProvider`` — subprocess ``claude -p``.
  Requires ``CLAUDE_CODE_OAUTH_TOKEN``. Slower (subprocess startup
  per call), parses JSON from free-form assistant text (with one
  repair retry on malformed output), passes PDFs/images via
  ``--add-dir`` + Read-tool prompt. Billed against Max subscription
  quota — free for the operator if they have a Max plan.

Selection rules (in priority order):

1. ``EDX_LLM_PROVIDER=claude_code`` → ClaudeCode (fail if no OAuth).
2. ``EDX_LLM_PROVIDER=anthropic`` → Anthropic (fail if no API key).
3. Unset, both keys present → Anthropic (faster, more reliable JSON).
4. Unset, only ``CLAUDE_CODE_OAUTH_TOKEN`` set → ClaudeCode.
5. Unset, only ``ANTHROPIC_API_KEY`` set → Anthropic.
6. Unset, neither set → ``LLMUnavailableError`` with operator hint.

OpenRouter was previously a silent fallback; per operator decision
(May 2026) it is no longer wired in — the operator prefers a single
billing channel.
"""

from __future__ import annotations

import os

from edx.config import AppSettings
from edx.providers.llm.anthropic_provider import AnthropicLLMProvider
from edx.providers.llm.base import LLMProvider, LLMUnavailableError
from edx.providers.llm.cache import CachedLLMProvider
from edx.providers.llm.claude_code_provider import (
    CLAUDE_OAUTH_ENV_VAR,
    ClaudeCodeLLMProvider,
)

PROVIDER_ENV_VAR = "EDX_LLM_PROVIDER"
_VALID_PROVIDERS = ("anthropic", "claude_code")


def _resolve_provider_choice(secrets_anthropic_set: bool) -> str:
    """Return the provider name to instantiate, applying the env-override
    + auto-pick rules in the module docstring."""
    explicit = os.environ.get(PROVIDER_ENV_VAR, "").strip().lower()
    if explicit:
        if explicit not in _VALID_PROVIDERS:
            raise LLMUnavailableError(
                f"{PROVIDER_ENV_VAR}={explicit!r} is not recognised; "
                f"valid values: {', '.join(_VALID_PROVIDERS)}"
            )
        return explicit
    has_oauth = bool(os.environ.get(CLAUDE_OAUTH_ENV_VAR))
    if has_oauth and not secrets_anthropic_set:
        return "claude_code"
    return "anthropic"


def build_llm_provider(settings: AppSettings) -> LLMProvider:
    """Return a single :class:`LLMProvider`, optionally wrapped in
    :class:`CachedLLMProvider` when ``llm.cache_enabled`` is true."""
    cfg = settings.llm
    secrets = settings.secrets

    primary_key = (
        secrets.anthropic_api_key.get_secret_value()
        if secrets.anthropic_api_key is not None
        else None
    )

    choice = _resolve_provider_choice(bool(primary_key))

    inner: LLMProvider
    if choice == "claude_code":
        if cfg.claude_code.enabled is False:
            raise LLMUnavailableError(
                "claude_code provider is disabled in config/llm.yaml; "
                "set llm.claude_code.enabled: true or pick a different "
                "EDX_LLM_PROVIDER."
            )
        # ``create()`` raises with a helpful hint if OAuth token missing.
        inner = ClaudeCodeLLMProvider.create(
            model=cfg.claude_code.model,
            timeout_seconds=cfg.claude_code.timeout_seconds,
            max_turns=cfg.claude_code.max_turns,
            max_repair_attempts=cfg.claude_code.max_repair_attempts,
        )
    else:  # "anthropic"
        if not (cfg.primary.enabled and primary_key):
            raise LLMUnavailableError(
                "No LLM provider configured. Either set ANTHROPIC_API_KEY "
                "(top up at console.anthropic.com) or set "
                f"{CLAUDE_OAUTH_ENV_VAR} (run `claude setup-token`) and "
                f"{PROVIDER_ENV_VAR}=claude_code in /opt/edx/.env.evolve."
            )
        inner = AnthropicLLMProvider.create(
            api_key=primary_key,
            model=cfg.primary.model,
            request_timeout_s=cfg.request_timeout_s,
            max_retries=cfg.max_retries,
            retry_min_wait_s=cfg.retry_min_wait_s,
            retry_max_wait_s=cfg.retry_max_wait_s,
            enable_prompt_caching=cfg.primary.enable_prompt_caching,
            cache_ttl=cfg.primary.cache_ttl,
        )

    if cfg.cache_enabled:
        cache_dir = settings.app.paths.processed_dir / "_llm_cache"
        return CachedLLMProvider(inner, cache_dir)
    return inner
