"""Build the LLM provider chain from settings + secrets."""

from __future__ import annotations

from edx.config import AppSettings
from edx.providers.llm.anthropic_provider import AnthropicLLMProvider
from edx.providers.llm.base import LLMProvider, LLMUnavailableError
from edx.providers.llm.cache import CachedLLMProvider
from edx.providers.llm.chain import FallbackChain
from edx.providers.llm.openrouter_provider import OpenRouterLLMProvider


def build_llm_provider(settings: AppSettings) -> LLMProvider:
    """Return a single :class:`LLMProvider` (potentially wrapping a chain).

    Rules:
    - If both keys are set → ``Anthropic`` is primary, ``OpenRouter`` is fallback.
    - If only ``ANTHROPIC_API_KEY`` is set → Anthropic alone.
    - If only ``OPENROUTER_API_KEY`` is set → OpenRouter alone.
    - If neither is set → :class:`LLMUnavailableError` with operator hint.

    The result is wrapped in :class:`CachedLLMProvider` when
    ``llm.cache_enabled`` is true.
    """
    cfg = settings.llm
    secrets = settings.secrets

    providers: list[LLMProvider] = []

    primary_key = (
        secrets.anthropic_api_key.get_secret_value()
        if secrets.anthropic_api_key is not None
        else None
    )
    fallback_key = (
        secrets.openrouter_api_key.get_secret_value()
        if secrets.openrouter_api_key is not None
        else None
    )

    if cfg.primary.enabled and primary_key:
        providers.append(
            AnthropicLLMProvider.create(
                api_key=primary_key,
                model=cfg.primary.model,
                request_timeout_s=cfg.request_timeout_s,
                max_retries=cfg.max_retries,
                retry_min_wait_s=cfg.retry_min_wait_s,
                retry_max_wait_s=cfg.retry_max_wait_s,
                enable_prompt_caching=cfg.primary.enable_prompt_caching,
                cache_ttl=cfg.primary.cache_ttl,
            )
        )

    if cfg.fallback.enabled and fallback_key:
        providers.append(
            OpenRouterLLMProvider.create(
                api_key=fallback_key,
                model=cfg.fallback.model,
                base_url=cfg.fallback.base_url,
                request_timeout_s=cfg.request_timeout_s,
                max_retries=cfg.max_retries,
                retry_min_wait_s=cfg.retry_min_wait_s,
                retry_max_wait_s=cfg.retry_max_wait_s,
            )
        )

    if not providers:
        raise LLMUnavailableError(
            "No LLM providers configured. Set ANTHROPIC_API_KEY (preferred) or "
            "OPENROUTER_API_KEY in .env, then re-run."
        )

    inner: LLMProvider = (
        FallbackChain(providers) if len(providers) > 1 else providers[0]
    )

    if cfg.cache_enabled:
        cache_dir = settings.app.paths.processed_dir / "_llm_cache"
        return CachedLLMProvider(inner, cache_dir)
    return inner
