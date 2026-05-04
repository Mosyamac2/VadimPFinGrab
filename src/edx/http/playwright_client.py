"""Playwright-backed drop-in replacement for :class:`EDisclosureClient`.

Why this exists
---------------
ServicePipe (the anti-bot in front of e-disclosure.ru) gates every page
behind a JavaScript challenge that issues a session cookie tied to the
TLS-fingerprint (JA3) of the client that solved the challenge. Plain
``httpx`` (Python's stdlib ``ssl``) negotiates a different TLS
handshake than Chromium — even with the right ``spsc``/``spid`` cookies
copied from a real browser, ServicePipe rejects them as "wrong client"
and serves the challenge page again (~1700 bytes instead of the 100+
KB real listing).

The fix is to talk to e-disclosure through a **real browser**: launch
headless Chromium once, let it solve the challenge in-page (so the
cookies are issued to a JA3-matching client), then route every
subsequent HTTP call through the **same** browser context using
Playwright's ``APIRequestContext``. ``context.request.get/post`` reuse
Chromium's TCP/TLS stack and the cookie jar that the JS challenge has
populated, so the JA3 matches and the cookies stick.

How this drops in
-----------------
The class subclasses :class:`EDisclosureClient` and overrides
``__aenter__``, ``__aexit__``, ``close``, ``get``, ``download`` —
nothing in the base class is invoked at runtime. The httpx state
created by ``super().__init__`` is never used; we keep the inheritance
chain only so callers that type-annotate ``EDisclosureClient`` (Discoverer,
Downloader, CLI) keep typechecking without a refactor.

Operation: roughly **one Chromium process** per ``edx update`` run,
~150–250 MB RAM, ~1–3 s startup for the initial challenge solve, ~50–
200 ms per subsequent ``get``. The Discoverer's 1 RPS pacing dominates
runtime, so the per-call overhead is invisible.

Selection
---------
Switch via ``app.discoverer.http_backend: playwright`` in
``config/app.yaml``. ``httpx`` (the default) keeps the old behaviour
for sites that don't fingerprint.

Install (once on the host)::

    pip install playwright
    playwright install chromium       # downloads the browser bundle
    playwright install-deps chromium  # apt-installs system libs (Linux)
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from edx.http.client import (
    DEFAULT_DOWNLOAD_CHUNK,
    DownloadResult,
    EDisclosureClient,
)
from edx.http.exceptions import ScrapeFailedError
from edx.logging_setup import get_logger


@dataclass(frozen=True)
class _PlaywrightResponse:
    """Subset of ``httpx.Response`` that downstream code actually reads."""

    status_code: int
    text: str
    content: bytes
    headers: dict[str, str]


class PlaywrightEDisclosureClient(EDisclosureClient):
    """Subclass that swaps the httpx transport for a real Chromium.

    All methods of the parent are overridden; the parent ``__init__`` is
    still called so the rate-limiter / user_agent / cookies attributes
    exist on the instance, but the internal ``httpx.AsyncClient`` is
    closed immediately and never used.
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
        cookies: dict[str, str] | None = None,
        # Bootstrap URL must be a *protected* page so ServicePipe actually
        # serves the JS-challenge during the in-browser warm-up; the
        # site's home (``/``) is unprotected and would leave the cookie
        # jar empty. ``/portal/files.aspx?id=3043&type=4`` (SBER МСФО)
        # works for every issuer because the challenge is tied to the
        # ``/portal/...`` namespace, not the specific ``id``.
        bootstrap_path: str = "/portal/files.aspx?id=3043&type=4",
    ) -> None:
        super().__init__(
            base_url=base_url,
            user_agent=user_agent,
            requests_per_second=requests_per_second,
            request_timeout_s=request_timeout_s,
            max_retries=max_retries,
            retry_min_wait_s=retry_min_wait_s,
            retry_max_wait_s=retry_max_wait_s,
            # Robots.txt under the Playwright backend is checked via the
            # browser too (not the parent's httpx-based RobotsCache); we
            # disable the parent's check to keep its httpx client idle.
            respect_robots=False,
            transport=None,
            cookies=cookies,
        )
        self._desired_respect_robots = respect_robots
        self._cookies_seed = cookies or {}
        self._request_timeout_ms = int(request_timeout_s * 1000)
        self._bootstrap_path = bootstrap_path
        # Filled in __aenter__.
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        # Re-used across get() calls — opening/closing a page per request
        # adds ~150 ms each and dwarfs the actual fetch.
        self._page: Any = None
        self._log = get_logger("edx.http.playwright_client")

    async def __aenter__(self) -> PlaywrightEDisclosureClient:
        try:
            from playwright.async_api import (
                async_playwright,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Playwright backend selected but the package isn't "
                "installed. Run: `pip install playwright && "
                "playwright install chromium`."
            ) from exc

        # Close the parent's idle httpx client up-front; we won't need it.
        await super().close()

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=self.user_agent,
            # Real-browser headers ServicePipe expects to see on the
            # initial challenge response.
            extra_http_headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        # Seed cookies from config so the challenge-solve step starts
        # already half-authenticated when the operator pasted fresh
        # values from a real browser session.
        if self._cookies_seed:
            host = self.base_url.split("://", 1)[-1].split("/", 1)[0]
            await self._context.add_cookies(
                [
                    {
                        "name": name,
                        "value": value,
                        "domain": host,
                        "path": "/",
                    }
                    for name, value in self._cookies_seed.items()
                ]
            )

        # Bootstrap on a *protected* URL so the JS-challenge actually
        # runs in the browser and populates the cookie jar. The home
        # page (``/``) is unprotected and would leave us empty-handed.
        # We reuse the same page object for every subsequent get() —
        # ServicePipe re-serves the challenge on each new URL within
        # the protected namespace, and only the browser can re-solve
        # it; ``context.request`` (Playwright's HTTP client) doesn't
        # execute JS, so it would loop on the challenge forever.
        self._page = await self._context.new_page()
        bootstrap_url = self.base_url.rstrip("/") + self._bootstrap_path
        await self._page.goto(
            bootstrap_url,
            wait_until="networkidle",
            timeout=self._request_timeout_ms,
        )

        if not self._desired_respect_robots:
            self._log.warning("robots_check_disabled")

        self._log.info(
            "playwright_client_started",
            base_url=self.base_url,
            bootstrap_url=bootstrap_url,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._page is not None:
            await self._page.close()
            self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def get(self, url: str) -> _PlaywrightResponse:  # type: ignore[override]
        """Fetch a protected page through the *browser*, not the HTTP
        client — ServicePipe re-serves the JS-challenge on each new URL
        in the ``/portal/...`` namespace, and only Chromium executes the
        JS that sets the per-page cookies. We always read the post-JS
        ``page.content()``, which is the same HTML a real user would
        see in DevTools → Elements.
        """
        full_url = url if url.startswith("http") else (
            self.base_url.rstrip("/") + url
        )
        if self._page is None:
            raise RuntimeError(
                "PlaywrightEDisclosureClient.get() called outside "
                "an `async with` block — the browser isn't started"
            )
        async with self._limiter:
            t0 = time.monotonic()
            response = await self._page.goto(
                full_url,
                wait_until="networkidle",
                timeout=self._request_timeout_ms,
            )
            # ``response`` is None on same-document navigations; for our
            # case (cross-URL navigations) it's always a Response object.
            content = await self._page.content()
            body = content.encode("utf-8")
            elapsed = time.monotonic() - t0
        status = response.status if response is not None else 200
        headers = dict(response.headers) if response is not None else {}
        self._log.info(
            "http_response",
            url=url,
            status=status,
            body_bytes=len(body),
            elapsed_s=round(elapsed, 4),
        )
        return _PlaywrightResponse(
            status_code=status,
            text=content,
            content=body,
            headers=headers,
        )

    async def download(
        self,
        url: str,
        target: Path,
        *,
        chunk_size: int = DEFAULT_DOWNLOAD_CHUNK,
    ) -> DownloadResult:
        del chunk_size  # context.request returns the full body in one shot
        full_url = url if url.startswith("http") else (
            self.base_url.rstrip("/") + url
        )
        if self._context is None:
            raise RuntimeError(
                "PlaywrightEDisclosureClient.download() called outside "
                "an `async with` block — the browser isn't started"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        if partial.exists():
            partial.unlink()

        async with self._limiter:
            t0 = time.monotonic()
            response = await self._context.request.get(
                full_url, timeout=self._request_timeout_ms
            )
            body = await response.body()
            elapsed = time.monotonic() - t0

        if response.status != 200:
            raise ScrapeFailedError(
                f"download failed: HTTP {response.status} for {url}"
            )

        sha = hashlib.sha256()
        sha.update(body)
        with open(partial, "wb") as fh:
            fh.write(body)
        os.replace(partial, target)

        self._log.info(
            "http_download",
            url=url,
            target=str(target),
            bytes=len(body),
            elapsed_s=round(elapsed, 4),
        )
        return DownloadResult(
            target=target,
            sha256=sha.hexdigest(),
            bytes_written=len(body),
            content_type=response.headers.get("content-type"),
            status_code=200,
        )
