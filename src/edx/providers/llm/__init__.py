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
from edx.providers.llm.claude_code_provider import ClaudeCodeLLMProvider
from edx.providers.llm.factory import build_llm_provider
from edx.providers.llm.openrouter_provider import OpenRouterLLMProvider

__all__ = [
    "AnthropicLLMProvider",
    "CachedLLMProvider",
    "ClaudeCodeLLMProvider",
    "FallbackChain",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMUnavailableError",
    "OpenRouterLLMProvider",
    "build_llm_provider",
    "request_cache_key",
]
