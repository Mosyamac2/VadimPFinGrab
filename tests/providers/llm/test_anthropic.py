"""Anthropic provider tests with a mocked SDK client."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest

from edx.providers.llm.anthropic_provider import AnthropicLLMProvider
from edx.providers.llm.base import LLMUnavailableError


def _success_response(
    payload: dict, *, in_tokens: int = 100, out_tokens: int = 20
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", name="extraction", input=payload)
        ],
        usage=SimpleNamespace(input_tokens=in_tokens, output_tokens=out_tokens),
    )


def _http_response(status: int) -> httpx.Response:
    """Anthropic exceptions need a request bound to the response."""
    return httpx.Response(
        status,
        text="",
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


def _make_provider(client: object) -> AnthropicLLMProvider:
    return AnthropicLLMProvider(
        client=client,  # type: ignore[arg-type]
        model="claude-sonnet-4-6",
        max_retries=2,
        retry_min_wait_s=0.0,
        retry_max_wait_s=0.01,
    )


@pytest.mark.asyncio
async def test_returns_parsed_tool_use_payload(request_factory: Callable[..., object]) -> None:
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_success_response({"metric": "revenue", "value": 12345.0})
            )
        )
    )
    provider = _make_provider(client)
    response = await provider.complete(request_factory())  # type: ignore[arg-type]
    assert response.provider == "anthropic"
    assert response.data == {"metric": "revenue", "value": 12345.0}
    assert response.input_tokens == 100
    assert response.output_tokens == 20
    assert client.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_authentication_error_becomes_llm_unavailable(
    request_factory: Callable[..., object],
) -> None:
    auth_error = anthropic.AuthenticationError(
        "bad key",
        response=_http_response(401),
        body={"error": "auth"},
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=auth_error))
    )
    provider = _make_provider(client)
    with pytest.raises(LLMUnavailableError, match="auth"):
        await provider.complete(request_factory())  # type: ignore[arg-type]
    assert client.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_internal_server_error_retries_then_succeeds(
    request_factory: Callable[..., object],
) -> None:
    server_error = anthropic.InternalServerError(
        "boom",
        response=_http_response(500),
        body=None,
    )
    create_mock = AsyncMock(
        side_effect=[
            server_error,
            server_error,
            _success_response({"metric": "x", "value": 1.0}),
        ]
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))
    provider = _make_provider(client)
    response = await provider.complete(request_factory())  # type: ignore[arg-type]
    assert response.data == {"metric": "x", "value": 1.0}
    assert create_mock.await_count == 3


@pytest.mark.asyncio
async def test_retries_exhausted_become_llm_unavailable(
    request_factory: Callable[..., object],
) -> None:
    server_error = anthropic.InternalServerError(
        "boom",
        response=_http_response(500),
        body=None,
    )
    create_mock = AsyncMock(side_effect=[server_error, server_error, server_error])
    client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))
    provider = _make_provider(client)
    with pytest.raises(LLMUnavailableError, match="exhausted"):
        await provider.complete(request_factory())  # type: ignore[arg-type]
    assert create_mock.await_count == 3


@pytest.mark.asyncio
async def test_response_without_tool_use_block_raises_unavailable(
    request_factory: Callable[..., object],
) -> None:
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="sorry no")],
                    usage=SimpleNamespace(input_tokens=10, output_tokens=2),
                )
            )
        )
    )
    provider = _make_provider(client)
    with pytest.raises(LLMUnavailableError, match="tool_use"):
        await provider.complete(request_factory())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pdf_input_passed_through(
    request_factory: Callable[..., object],
) -> None:
    captured: dict = {}

    async def _fake_create(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _success_response({"metric": "x", "value": 1.0})

    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=_fake_create))
    )
    provider = _make_provider(client)
    await provider.complete(request_factory(pdf_bytes=b"%PDF-1.4 fake"))  # type: ignore[arg-type]

    user_content = captured["messages"][0]["content"]
    types = [block["type"] for block in user_content]
    assert "document" in types
    # Cache control on the system block when prompt caching is enabled.
    system_blocks = captured["system"]
    assert system_blocks[0].get("cache_control") == {"type": "ephemeral"}
