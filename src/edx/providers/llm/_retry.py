"""Shared tenacity wait builder for LLM providers."""

from __future__ import annotations

from typing import Any

from tenacity import RetryCallState, wait_exponential


def build_llm_wait(min_wait_s: float, max_wait_s: float) -> Any:
    """Exponential backoff with sensible bounds for LLM retries."""
    base = wait_exponential(multiplier=min_wait_s, min=min_wait_s, max=max_wait_s)

    def waiter(state: RetryCallState) -> float:
        return float(base(state))

    return waiter
