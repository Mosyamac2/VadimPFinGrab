"""LLM provider abstraction (ТЗ §17 — двухканальный доступ + fallback)."""

from edx.providers.llm.anthropic_provider import AnthropicLLMProvider
from edx.providers.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)
from edx.providers.llm.cache import CachedLLMProvider, request_cache_key
from edx.providers.llm.chain import FallbackChain
from edx.providers.llm.factory import build_llm_provider
from edx.providers.llm.openrouter_provider import OpenRouterLLMProvider

__all__ = [
    "AnthropicLLMProvider",
    "CachedLLMProvider",
    "FallbackChain",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMUnavailableError",
    "OpenRouterLLMProvider",
    "build_llm_provider",
    "request_cache_key",
]
