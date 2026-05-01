"""Factory for the Downloader stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.http.client import EDisclosureClient
from edx.stages.downloader.service import DownloaderService
from edx.storage import PublicationsRepo


def build_downloader_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    *,
    client: EDisclosureClient,
) -> DownloaderService:
    cfg = settings.app.downloader
    return DownloaderService(
        client=client,
        publications_repo=publications_repo,
        raw_dir=settings.app.paths.raw_dir,
        concurrency=cfg.concurrency,
        follow_html_links=cfg.follow_html_links,
        chunk_size_bytes=cfg.chunk_size_bytes,
    )
