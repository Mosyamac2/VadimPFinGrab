"""robots.txt enforcement."""

from __future__ import annotations

import httpx
import pytest

from edx.http.client import EDisclosureClient
from edx.http.exceptions import RobotsDisallowedError


def _make_transport(robots_body: str, robots_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots_body)
        return httpx.Response(200, text="<html>ok</html>")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_disallow_all_raises_robots_disallowed_error() -> None:
    transport = _make_transport("User-agent: *\nDisallow: /\n")
    async with EDisclosureClient(
        base_url="https://example.test",
        user_agent="edx-test/1.0",
        requests_per_second=10.0,
        max_retries=0,
        respect_robots=True,
        transport=transport,
    ) as client:
        with pytest.raises(RobotsDisallowedError):
            await client.get("https://example.test/anywhere")


@pytest.mark.asyncio
async def test_allow_explicit_path() -> None:
    transport = _make_transport(
        "User-agent: *\nDisallow: /private/\nAllow: /portal/\n"
    )
    async with EDisclosureClient(
        base_url="https://example.test",
        user_agent="edx-test/1.0",
        requests_per_second=10.0,
        max_retries=0,
        respect_robots=True,
        transport=transport,
    ) as client:
        response = await client.get("https://example.test/portal/page")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_disabled_robots_skips_check_even_for_disallowed_url() -> None:
    transport = _make_transport("User-agent: *\nDisallow: /\n")
    async with EDisclosureClient(
        base_url="https://example.test",
        user_agent="edx-test/1.0",
        requests_per_second=10.0,
        max_retries=0,
        respect_robots=False,
        transport=transport,
    ) as client:
        # respect_robots=False short-circuits the check.
        response = await client.get("https://example.test/anywhere")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_robots_404_treated_as_allow_all() -> None:
    transport = _make_transport("not-found", robots_status=404)
    async with EDisclosureClient(
        base_url="https://example.test",
        user_agent="edx-test/1.0",
        requests_per_second=10.0,
        max_retries=0,
        respect_robots=True,
        transport=transport,
    ) as client:
        response = await client.get("https://example.test/page")
        assert response.status_code == 200
