"""Common fixtures for LLM provider tests."""

from __future__ import annotations

from typing import Any

import pytest

from edx.providers.llm.base import LLMRequest


@pytest.fixture
def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "metric": {"type": "string"},
            "value": {"type": "number"},
        },
        "required": ["metric", "value"],
        "additionalProperties": False,
    }


@pytest.fixture
def request_factory(schema: dict[str, Any]) -> object:
    def _factory(
        *,
        system: str = "You are an extractor.",
        user_text: str = "Extract revenue.",
        pdf_bytes: bytes | None = None,
        pdf_page_indices: tuple[int, ...] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMRequest:
        return LLMRequest(
            system=system,
            user_text=user_text,
            pdf_bytes=pdf_bytes,
            pdf_page_indices=pdf_page_indices,
            json_schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    return _factory
