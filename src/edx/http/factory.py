"""Pick the right HTTP backend per ``app.discoverer.http_backend``.

Centralises the if/else that used to live in three call-sites
(Discoverer factory, CLI ``edx update``, CLI per-stage subcommands)
into one helper. Add a new backend here and every caller picks it up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from edx.http.client import EDisclosureClient, build_user_agent

if TYPE_CHECKING:
    from edx.config import AppSettings


def build_http_client(
    settings: AppSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EDisclosureClient:
    """Construct the configured HTTP client (httpx or Playwright-backed).

    ``transport`` is forwarded only to the httpx backend — Playwright
    runs a real Chromium and ignores it.
    """
    cfg = settings.app.discoverer
    if cfg.http_backend == "playwright":
        # Imported lazily so the dependency stays optional: missing
        # ``playwright`` shouldn't break the default httpx path or the
        # full pytest suite on a fresh checkout.
        from edx.http.playwright_client import PlaywrightEDisclosureClient

        return PlaywrightEDisclosureClient(
            base_url=cfg.base_url,
            user_agent=build_user_agent(settings),
            requests_per_second=cfg.requests_per_second,
            request_timeout_s=cfg.request_timeout_s,
            max_retries=cfg.max_retries,
            retry_min_wait_s=cfg.retry_min_wait_s,
            retry_max_wait_s=cfg.retry_max_wait_s,
            respect_robots=cfg.respect_robots,
            cookies=cfg.cookies or None,
        )

    return EDisclosureClient(
        base_url=cfg.base_url,
        user_agent=build_user_agent(settings),
        requests_per_second=cfg.requests_per_second,
        request_timeout_s=cfg.request_timeout_s,
        max_retries=cfg.max_retries,
        retry_min_wait_s=cfg.retry_min_wait_s,
        retry_max_wait_s=cfg.retry_max_wait_s,
        respect_robots=cfg.respect_robots,
        transport=transport,
        cookies=cfg.cookies or None,
    )
