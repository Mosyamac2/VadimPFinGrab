"""Factory: assemble Discoverer stage from ``AppSettings`` + repositories."""

from __future__ import annotations

import httpx

from edx.config import AppSettings
from edx.http import EDisclosureClient, build_http_client
from edx.stages.discoverer.service import DiscovererService
from edx.storage import PublicationsRepo


def build_edisclosure_client(
    settings: AppSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EDisclosureClient:
    """Patch 23: thin alias kept for back-compat. Dispatches to the
    configured backend via :func:`edx.http.build_http_client`."""
    return build_http_client(settings, transport=transport)


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
