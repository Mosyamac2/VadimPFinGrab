"""Strict-pacing rate limit on EDisclosureClient."""

from __future__ import annotations

import time

import httpx
import pytest

from edx.http.client import EDisclosureClient


def _ok_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_5_requests_at_2_rps_takes_at_least_2_seconds() -> None:
    async with EDisclosureClient(
        base_url="https://example.test",
        user_agent="edx-test/1.0",
        requests_per_second=2.0,
        max_retries=0,
        respect_robots=False,
        transport=_ok_transport(),
    ) as client:
        start = time.monotonic()
        for i in range(5):
            response = await client.get(f"https://example.test/page-{i}")
            assert response.status_code == 200
        elapsed = time.monotonic() - start

    # 5 calls at strict 2 rps => 4 inter-request gaps of 0.5s each = 2.0s.
    assert elapsed >= 2.0, f"expected >= 2.0s, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_invalid_rate_rejected() -> None:
    with pytest.raises(ValueError):
        EDisclosureClient(
            base_url="https://example.test",
            user_agent="x",
            requests_per_second=0.0,
            transport=_ok_transport(),
        )
