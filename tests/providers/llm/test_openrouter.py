"""OpenRouter provider tests with httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from edx.providers.llm.base import LLMUnavailableError
from edx.providers.llm.openrouter_provider import OpenRouterLLMProvider


def _provider(transport: httpx.MockTransport, **overrides: object) -> OpenRouterLLMProvider:
    defaults: dict[str, object] = {
        "api_key": "or-test",
        "model": "anthropic/claude-sonnet-4.6",
        "base_url": "https://openrouter.ai/api/v1",
        "request_timeout_s": 5.0,
        "max_retries": 2,
        "retry_min_wait_s": 0.0,
        "retry_max_wait_s": 0.01,
        "transport": transport,
    }
    defaults.update(overrides)
    return OpenRouterLLMProvider.create(**defaults)  # type: ignore[arg-type]


def _envelope(content: str, *, prompt_tokens: int = 50, completion_tokens: int = 10) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


@pytest.mark.asyncio
async def test_success_returns_parsed_json(request_factory: Callable[..., object]) -> None:
    body = _envelope(json.dumps({"metric": "revenue", "value": 100.0}))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=body)
    )
    provider = _provider(transport)
    try:
        response = await provider.complete(request_factory())  # type: ignore[arg-type]
    finally:
        await provider.aclose()
    assert response.provider == "openrouter"
    assert response.data == {"metric": "revenue", "value": 100.0}
    assert response.input_tokens == 50
    assert response.output_tokens == 10


@pytest.mark.asyncio
async def test_503_503_then_200_returns_with_two_retries(
    request_factory: Callable[..., object],
) -> None:
    sequence: list[httpx.Response] = [
        httpx.Response(503, text="busy"),
        httpx.Response(503, text="busy"),
        httpx.Response(
            200, json=_envelope(json.dumps({"metric": "x", "value": 1.0}))
        ),
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return sequence.pop(0)

    transport = httpx.MockTransport(handler)
    provider = _provider(transport)
    try:
        response = await provider.complete(request_factory())  # type: ignore[arg-type]
    finally:
        await provider.aclose()
    assert response.data == {"metric": "x", "value": 1.0}
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_401_becomes_llm_unavailable(
    request_factory: Callable[..., object],
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, text="invalid key")
    )
    provider = _provider(transport)
    try:
        with pytest.raises(LLMUnavailableError, match="HTTP 401"):
            await provider.complete(request_factory())  # type: ignore[arg-type]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_invalid_json_repaired_via_jsonrepair(
    request_factory: Callable[..., object],
) -> None:
    # Trailing comma + missing quotes — json.loads fails, json-repair fixes it.
    bad_content = '```json\n{metric: "revenue", "value": 100,}\n```'
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=_envelope(bad_content))
    )
    provider = _provider(transport)
    try:
        response = await provider.complete(request_factory())  # type: ignore[arg-type]
    finally:
        await provider.aclose()
    assert response.data["metric"] == "revenue"
    assert response.data["value"] == 100


@pytest.mark.asyncio
async def test_unrepairable_content_raises_llm_unavailable(
    request_factory: Callable[..., object],
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=_envelope(""))
    )
    provider = _provider(transport)
    try:
        with pytest.raises(LLMUnavailableError):
            await provider.complete(request_factory())  # type: ignore[arg-type]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_5xx_exhaustion_becomes_llm_unavailable(
    request_factory: Callable[..., object],
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, text="busy"),
    )
    provider = _provider(transport, max_retries=2)
    try:
        with pytest.raises(LLMUnavailableError, match="exhausted retries"):
            await provider.complete(request_factory())  # type: ignore[arg-type]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_pdf_bytes_dropped_with_warning(
    request_factory: Callable[..., object],
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=_envelope(json.dumps({"metric": "x", "value": 1.0}))
        )
    )
    provider = _provider(transport)
    try:
        # Should not raise — PDF is silently dropped, request still succeeds.
        response = await provider.complete(
            request_factory(pdf_bytes=b"%PDF fake")  # type: ignore[arg-type]
        )
        assert response.data == {"metric": "x", "value": 1.0}
    finally:
        await provider.aclose()
