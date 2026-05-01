"""Retry behaviour on transport errors and retryable HTTP statuses."""

from __future__ import annotations

import httpx
import pytest

from edx.http.client import EDisclosureClient


class _SequenceTransport(httpx.MockTransport):
    """Transport that responds with a pre-recorded sequence per URL."""

    def __init__(self, sequence: list[httpx.Response | Exception]) -> None:
        self._sequence = list(sequence)
        self.calls = 0
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if not self._sequence:
            return httpx.Response(200, text="default-ok")
        item = self._sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(transport: httpx.MockTransport, **overrides: object) -> EDisclosureClient:
    defaults: dict[str, object] = {
        "base_url": "https://example.test",
        "user_agent": "edx-test/1.0",
        "requests_per_second": 100.0,  # effectively unbounded for tests
        "max_retries": 3,
        "retry_min_wait_s": 0.0,
        "retry_max_wait_s": 0.01,
        "respect_robots": False,
        "transport": transport,
    }
    defaults.update(overrides)
    return EDisclosureClient(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_503_503_then_200_returns_200_with_two_retries() -> None:
    transport = _SequenceTransport(
        [
            httpx.Response(503, text="busy"),
            httpx.Response(503, text="busy"),
            httpx.Response(200, text="finally"),
        ]
    )
    async with _client(transport) as client:
        response = await client.get("https://example.test/foo")
    assert response.status_code == 200
    assert response.text == "finally"
    assert transport.calls == 3  # one initial + two retries


@pytest.mark.asyncio
async def test_429_with_retry_after_is_retried() -> None:
    transport = _SequenceTransport(
        [
            httpx.Response(429, headers={"Retry-After": "0"}, text="rate"),
            httpx.Response(200, text="ok"),
        ]
    )
    async with _client(transport) as client:
        response = await client.get("https://example.test/x")
    assert response.status_code == 200
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_transport_error_retried_then_success() -> None:
    transport = _SequenceTransport(
        [
            httpx.ConnectError("network down"),
            httpx.Response(200, text="ok"),
        ]
    )
    async with _client(transport) as client:
        response = await client.get("https://example.test/x")
    assert response.status_code == 200
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_transport_error_propagates_after_exhausting_retries() -> None:
    transport = _SequenceTransport(
        [
            httpx.ConnectError("a"),
            httpx.ConnectError("b"),
            httpx.ConnectError("c"),
            httpx.ConnectError("d"),
        ]
    )
    async with _client(transport, max_retries=2) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://example.test/x")
    assert transport.calls == 3  # one initial + two retries


@pytest.mark.asyncio
async def test_5xx_retries_exhausted_returns_final_response() -> None:
    transport = _SequenceTransport(
        [
            httpx.Response(503, text="b"),
            httpx.Response(503, text="b"),
            httpx.Response(503, text="b"),
        ]
    )
    async with _client(transport, max_retries=2) as client:
        response = await client.get("https://example.test/x")
    # On retryable HTTP statuses, after retries exhaust, we surface the last response.
    assert response.status_code == 503
    assert transport.calls == 3
