"""Factory: assemble Discoverer stage from ``AppSettings`` + repositories."""

from __future__ import annotations

import httpx

from edx.config import AppSettings
from edx.http.client import EDisclosureClient, build_user_agent
from edx.stages.discoverer.service import DiscovererService
from edx.storage import PublicationsRepo


def build_edisclosure_client(
    settings: AppSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EDisclosureClient:
    cfg = settings.app.discoverer
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


def build_discoverer_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    *,
    client: EDisclosureClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[DiscovererService, EDisclosureClient]:
    """Returns the service and the underlying client; caller closes the client."""
    http_client = client or build_edisclosure_client(settings, transport=transport)
    service = DiscovererService(
        client=http_client,
        publications_repo=publications_repo,
        backfill_years=settings.app.mode.backfill_years,
    )
    return service, http_client
