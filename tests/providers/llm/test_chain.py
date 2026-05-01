"""FallbackChain tests with simple stub providers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from edx.providers.llm.base import (
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)
from edx.providers.llm.chain import FallbackChain


@dataclass
class _StubProvider:
    name: str
    supports_pdf_input: bool = False
    fail_with: BaseException | None = None
    response_data: dict[str, Any] = field(default_factory=lambda: {"metric": "x", "value": 1.0})
    calls: list[LLMRequest] = field(default_factory=list)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        if self.fail_with is not None:
            raise self.fail_with
        return LLMResponse(
            data=self.response_data,
            raw_text="ok",
            provider=self.name,
            model="m",
            input_tokens=1,
            output_tokens=1,
        )


@pytest.mark.asyncio
async def test_primary_success_short_circuits_fallback(
    request_factory: Callable[..., LLMRequest],
) -> None:
    primary = _StubProvider(name="primary", supports_pdf_input=True)
    fallback = _StubProvider(name="fallback")
    chain = FallbackChain([primary, fallback])
    assert chain.supports_pdf_input is True
    response = await chain.complete(request_factory())
    assert response.provider == "primary"
    assert len(primary.calls) == 1
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_unavailable_falls_back_to_secondary(
    request_factory: Callable[..., LLMRequest],
) -> None:
    primary = _StubProvider(
        name="primary",
        fail_with=LLMUnavailableError("primary down"),
    )
    fallback = _StubProvider(name="fallback")
    chain = FallbackChain([primary, fallback])
    response = await chain.complete(request_factory())
    assert response.provider == "fallback"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_transport_error_also_triggers_fallback(
    request_factory: Callable[..., LLMRequest],
) -> None:
    primary = _StubProvider(
        name="primary",
        fail_with=httpx.ConnectError("network down"),
    )
    fallback = _StubProvider(name="fallback")
    chain = FallbackChain([primary, fallback])
    response = await chain.complete(request_factory())
    assert response.provider == "fallback"


@pytest.mark.asyncio
async def test_both_providers_fail_propagates(
    request_factory: Callable[..., LLMRequest],
) -> None:
    primary = _StubProvider(
        name="primary",
        fail_with=LLMUnavailableError("primary down"),
    )
    fallback = _StubProvider(
        name="fallback",
        fail_with=LLMUnavailableError("fallback down"),
    )
    chain = FallbackChain([primary, fallback])
    with pytest.raises(LLMUnavailableError, match="fallback down"):
        await chain.complete(request_factory())


def test_empty_chain_rejected() -> None:
    with pytest.raises(ValueError):
        FallbackChain([])
