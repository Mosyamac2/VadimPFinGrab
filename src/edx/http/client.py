"""Polite async HTTP client used by the Discoverer/Downloader stages.

Combines:
- ``aiolimiter.AsyncLimiter`` configured for *strict pacing* (no burst): one
  request per ``1 / requests_per_second`` seconds.
- ``tenacity`` retries on ``httpx.TransportError``, 5xx, and 429 — honouring
  the ``Retry-After`` header where present.
- ``RobotsCache`` for ``robots.txt`` enforcement (can be disabled via config).
- ``structlog`` lines per response with URL/status/body size/elapsed time.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from edx import __version__
from edx.config import AppSettings
from edx.http.exceptions import RobotsDisallowedError, ScrapeFailedError
from edx.http.robots import RobotsCache
from edx.logging_setup import get_logger

RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
DEFAULT_DOWNLOAD_CHUNK = 64 * 1024


class _RetryableHTTPError(Exception):
    """Internal marker to drive tenacity retry on retryable HTTP statuses."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"retryable HTTP status {response.status_code}")


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of :meth:`EDisclosureClient.download`."""

    target: Path
    sha256: str
    bytes_written: int
    content_type: str | None
    status_code: int


def build_user_agent(settings: AppSettings) -> str:
    """Build a ``User-Agent`` header from app settings.

    Resolution order:
    1. ``app.discoverer.user_agent`` if set — verbatim override (e.g. when a
       site's anti-bot rules block obvious bot UAs and you need a browser one).
    2. Otherwise ``edx/<version> (+e-disclosure-extractor)``, with optional
       ``contact=<email>`` appended when ``app.contact_email`` is set.
    """
    override = settings.app.discoverer.user_agent
    if override:
        return override
    base = f"edx/{__version__} (+e-disclosure-extractor)"
    contact = settings.app.contact_email
    if contact:
        return f"{base}; contact={contact}"
    return base


class EDisclosureClient:
    """Polite, rate-limited, retry-capable async HTTP client.

    Typical usage::

        async with EDisclosureClient(user_agent="...", ...) as client:
            response = await client.get(url)
    """

    def __init__(
        self,
        *,
        base_url: str = "https://www.e-disclosure.ru",
        user_agent: str,
        requests_per_second: float = 1.0,
        request_timeout_s: float = 30.0,
        max_retries: int = 3,
        retry_min_wait_s: float = 0.5,
        retry_max_wait_s: float = 10.0,
        respect_robots: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        self.base_url = base_url
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self._max_retries = max_retries
        self._retry_min_wait_s = retry_min_wait_s
        self._retry_max_wait_s = retry_max_wait_s
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": user_agent},
            timeout=request_timeout_s,
            transport=transport,
            follow_redirects=True,
        )
        # Strict pacing — one request per (1/rps) seconds, no burst.
        self._limiter = AsyncLimiter(
            max_rate=1, time_period=1.0 / requests_per_second
        )
        self._robots = RobotsCache(self._client, user_agent)
        self._log = get_logger("edx.http.client")
        if not respect_robots:
            self._log.warning("robots_check_disabled")

    async def __aenter__(self) -> EDisclosureClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def download(
        self,
        url: str,
        target: Path,
        *,
        chunk_size: int = DEFAULT_DOWNLOAD_CHUNK,
    ) -> DownloadResult:
        """Stream ``url`` into ``target``, with retries and atomic replace.

        Internals:
        - Robots check is honoured (same as :meth:`get`).
        - Body is streamed into ``{target}.partial``; on any error the partial
          is removed before the next retry.
        - On success the partial is renamed atomically to ``target``.
        - SHA-256 is computed on the fly so we don't re-read the file.
        - 5xx/429 bodies are drained, then a tenacity retry is triggered;
          ``Retry-After`` is honoured by ``_build_wait_strategy``.
        - After retries are exhausted, raises ``ScrapeFailedError``.
        """
        if self.respect_robots:
            allowed = await self._robots.is_allowed(url)
            if not allowed:
                raise RobotsDisallowedError(url)

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")

        async def _attempt() -> DownloadResult:
            if partial.exists():
                partial.unlink()
            async with self._limiter:
                t0 = time.monotonic()
                try:
                    async with self._client.stream("GET", url) as response:
                        if response.status_code in RETRYABLE_STATUSES:
                            await response.aread()
                            raise _RetryableHTTPError(response)
                        if response.status_code != 200:
                            await response.aread()
                            raise ScrapeFailedError(
                                f"HTTP {response.status_code} for {url}"
                            )

                        sha = hashlib.sha256()
                        bytes_written = 0
                        with open(partial, "wb") as fh:
                            async for chunk in response.aiter_bytes(chunk_size):
                                fh.write(chunk)
                                sha.update(chunk)
                                bytes_written += len(chunk)
                        content_type = response.headers.get("Content-Type")
                except BaseException:
                    if partial.exists():
                        partial.unlink()
                    raise
                elapsed = time.monotonic() - t0

            os.replace(partial, target)
            self._log.info(
                "http_download",
                url=url,
                target=str(target),
                bytes=bytes_written,
                elapsed_s=round(elapsed, 4),
            )
            return DownloadResult(
                target=target,
                sha256=sha.hexdigest(),
                bytes_written=bytes_written,
                content_type=content_type,
                status_code=200,
            )

        retrier: AsyncRetrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=self._build_wait_strategy(),
            retry=retry_if_exception_type(
                (httpx.TransportError, _RetryableHTTPError)
            ),
            reraise=True,
        )
        try:
            result: DownloadResult = await retrier(_attempt)
        except _RetryableHTTPError as exc:
            if partial.exists():
                partial.unlink()
            raise ScrapeFailedError(
                f"download failed after retries: HTTP "
                f"{exc.response.status_code} for {url}"
            ) from exc
        return result

    async def get(self, url: str) -> httpx.Response:
        """Idempotent GET with rate-limit, robots-check, and retry on transient errors."""
        if self.respect_robots:
            allowed = await self._robots.is_allowed(url)
            if not allowed:
                raise RobotsDisallowedError(url)

        async def _attempt() -> httpx.Response:
            async with self._limiter:
                t0 = time.monotonic()
                response = await self._client.get(url)
                elapsed = time.monotonic() - t0
            self._log.info(
                "http_response",
                url=url,
                status=response.status_code,
                body_bytes=len(response.content),
                elapsed_s=round(elapsed, 4),
            )
            if response.status_code in RETRYABLE_STATUSES:
                raise _RetryableHTTPError(response)
            return response

        retrier: AsyncRetrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=self._build_wait_strategy(),
            retry=retry_if_exception_type(
                (httpx.TransportError, _RetryableHTTPError)
            ),
            reraise=True,
        )
        try:
            result: httpx.Response = await retrier(_attempt)
        except _RetryableHTTPError as exc:
            # Retries exhausted on retryable status — surface the final response
            # so callers can decide to fail-soft or escalate.
            return exc.response
        return result

    def _build_wait_strategy(self) -> Any:
        base = wait_exponential(
            multiplier=self._retry_min_wait_s,
            min=self._retry_min_wait_s,
            max=self._retry_max_wait_s,
        )

        def waiter(state: RetryCallState) -> float:
            outcome = state.outcome
            if outcome is not None and outcome.failed:
                exc = outcome.exception()
                if isinstance(exc, _RetryableHTTPError):
                    retry_after = exc.response.headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            return min(
                                float(retry_after), self._retry_max_wait_s
                            )
                        except ValueError:
                            pass
            wait_s = base(state)
            return float(wait_s)

        return waiter
