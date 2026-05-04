"""Build the LLM provider chain from settings + secrets.

Per operator decision (May 2026): the pipeline routes ALL LLM calls
through the operator's Anthropic API account. The OpenRouter fallback
that this module used to wire in has been removed — the operator
prefers a single billing channel and would rather see fast-fail HTTP
402 from Anthropic than a silent fallback that drains a second vendor
account.

If/when the pipeline is migrated to spawn ``claude -p`` for Max-OAuth
billing, this is where the new ``ClaudeCodeLLMProvider`` would slot
in alongside (or instead of) ``AnthropicLLMProvider``.
"""

from __future__ import annotations

from edx.config import AppSettings
from edx.providers.llm.anthropic_provider import AnthropicLLMProvider
from edx.providers.llm.base import LLMProvider, LLMUnavailableError
from edx.providers.llm.cache import CachedLLMProvider


def build_llm_provider(settings: AppSettings) -> LLMProvider:
    """Return a single :class:`LLMProvider`.

    Rules:
    - ``ANTHROPIC_API_KEY`` set → Anthropic is the only provider.
    - ``ANTHROPIC_API_KEY`` missing → :class:`LLMUnavailableError` with
      an operator hint (top up at console.anthropic.com).

    The result is wrapped in :class:`CachedLLMProvider` when
    ``llm.cache_enabled`` is true.
    """
    cfg = settings.llm
    secrets = settings.secrets

    primary_key = (
        secrets.anthropic_api_key.get_secret_value()
        if secrets.anthropic_api_key is not None
        else None
    )

    if not (cfg.primary.enabled and primary_key):
        raise LLMUnavailableError(
            "No LLM provider configured. Set ANTHROPIC_API_KEY in .env "
            "(top up at console.anthropic.com), then re-run."
        )

    inner: LLMProvider = AnthropicLLMProvider.create(
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
