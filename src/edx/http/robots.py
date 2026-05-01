"""``robots.txt`` cache, scoped to one origin (scheme + host[:port])."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from edx.logging_setup import get_logger


class RobotsCache:
    """Per-origin ``robots.txt`` cache. Loaded once per process."""

    def __init__(self, http_client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = http_client
        self._user_agent = user_agent
        self._cache: dict[str, RobotFileParser] = {}
        self._lock = asyncio.Lock()
        self._log = get_logger("edx.http.robots")

    async def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return True
        origin = f"{parsed.scheme}://{parsed.netloc}"
        async with self._lock:
            parser = self._cache.get(origin)
            if parser is None:
                parser = await self._fetch(origin)
                self._cache[origin] = parser
        return parser.can_fetch(self._user_agent, url)

    async def _fetch(self, origin: str) -> RobotFileParser:
        url = urljoin(origin + "/", "robots.txt")
        parser = RobotFileParser()
        try:
            response = await self._client.get(url, timeout=10.0)
        except httpx.HTTPError as exc:
            self._log.warning("robots_fetch_failed", origin=origin, error=str(exc))
            parser.allow_all = True  # type: ignore[attr-defined]
            return parser

        if response.status_code == 200:
            parser.parse(response.text.splitlines())
            self._log.info("robots_loaded", origin=origin)
        elif 400 <= response.status_code < 500:
            self._log.info(
                "robots_treated_as_allow_all",
                origin=origin,
                status=response.status_code,
            )
            parser.allow_all = True  # type: ignore[attr-defined]
        else:
            self._log.warning(
                "robots_unavailable",
                origin=origin,
                status=response.status_code,
            )
            parser.disallow_all = True  # type: ignore[attr-defined]
        return parser
