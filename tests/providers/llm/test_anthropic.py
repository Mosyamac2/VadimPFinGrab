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
    payload: dict,
    *,
    in_tokens: int = 100,
    out_tokens: int = 20,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", name="extraction", input=payload)
        ],
        usage=SimpleNamespace(
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        ),
    )


def _http_response(status: int) -> httpx.Response:
    """Anthropic exceptions need a request bound to the response."""
    return httpx.Response(
        status,
        text="",
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


def _make_provider(
    client: object,
    *,
    enable_prompt_caching: bool = True,
    cache_ttl: str = "5m",
) -> AnthropicLLMProvider:
    # Tests pin cache_ttl="5m" so the default cache_control payload stays
    # ``{"type": "ephemeral"}`` (no ttl key) — keeps assertion shape
    # straightforward. Patch 28's 1h-TTL behaviour is covered by its
    # dedicated test below.
    return AnthropicLLMProvider(
        client=client,  # type: ignore[arg-type]
        model="claude-sonnet-4-6",
        max_retries=2,
        retry_min_wait_s=0.0,
        retry_max_wait_s=0.01,
        enable_prompt_caching=enable_prompt_caching,
        cache_ttl=cache_ttl,
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


# ---------------------------------------------------------------------------
# Patch 28: prompt-cache observability + 1-hour TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_metrics_extracted_from_usage(
    request_factory: Callable[..., object],
) -> None:
    """``cache_read_input_tokens`` / ``cache_creation_input_tokens``
    from the SDK ``usage`` object must surface on ``LLMResponse``.
    Without these the operator can't tell whether the system prompt
    is actually hitting cache between extractor calls.
    """
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_success_response(
                    {"metric": "revenue", "value": 1.0},
                    in_tokens=12345,
                    out_tokens=42,
                    cache_read=10000,
                    cache_creation=2000,
                )
            )
        )
    )
    provider = _make_provider(client)
    response = await provider.complete(request_factory())  # type: ignore[arg-type]
    assert response.cache_read_input_tokens == 10000
    assert response.cache_creation_input_tokens == 2000
    # Non-cache fields are unchanged.
    assert response.input_tokens == 12345
    assert response.output_tokens == 42


@pytest.mark.asyncio
async def test_cache_metrics_default_to_zero_when_usage_omits_them(
    request_factory: Callable[..., object],
) -> None:
    """Older SDK responses (or non-cached calls) lack these fields —
    ``getattr`` with default 0 keeps the response well-formed.
    """
    # Build a usage object *without* the cache_* attributes at all.
    usage = SimpleNamespace(input_tokens=50, output_tokens=10)
    response_obj = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use", name="extraction", input={"x": 1.0}
            )
        ],
        usage=usage,
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=response_obj))
    )
    provider = _make_provider(client)
    response = await provider.complete(request_factory())  # type: ignore[arg-type]
    assert response.cache_read_input_tokens == 0
    assert response.cache_creation_input_tokens == 0


@pytest.mark.asyncio
async def test_one_hour_ttl_flows_into_cache_control(
    request_factory: Callable[..., object],
) -> None:
    """``cache_ttl="1h"`` must add ``ttl: 1h`` to the system-block
    cache_control. The 5m default omits the key (preserving wire
    compatibility with pre-Patch-28 request bodies).
    """
    captured: dict = {}

    async def _fake_create(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _success_response({"x": 1.0})

    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=_fake_create))
    )
    provider = _make_provider(client, cache_ttl="1h")
    await provider.complete(request_factory())  # type: ignore[arg-type]

    system_blocks = captured["system"]
    assert system_blocks[0]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }


@pytest.mark.asyncio
async def test_caching_disabled_omits_cache_control_block(
    request_factory: Callable[..., object],
) -> None:
    """``enable_prompt_caching=False`` must produce a plain text
    system block with no ``cache_control`` key — independent of
    ``cache_ttl``.
    """
    captured: dict = {}

    async def _fake_create(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _success_response({"x": 1.0})

    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=_fake_create))
    )
    provider = _make_provider(
        client, enable_prompt_caching=False, cache_ttl="1h"
    )
    await provider.complete(request_factory())  # type: ignore[arg-type]

    system_blocks = captured["system"]
    assert "cache_control" not in system_blocks[0]
