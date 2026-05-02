"""Downloader stage: per-publication async downloads under a semaphore.

Behaviour:
- Each publication maps to ``data/raw/{ticker}/{publication_id}/``.
- The primary file (derived from ``source_url``) is streamed to disk.
- If the response is HTML and ``follow_html_links`` is enabled, the page is
  scanned for links to documents (PDF/DOC/RAR/ZIP/etc.) and each is downloaded
  into the ``linked/`` subdirectory.
- Idempotent on file-exists + matching ``publications.file_hash``: re-running
  the stage over the same publication is a no-op (logged as
  ``download_skipped_identical_hash``).
- Any partially-written ``*.partial`` file from an interrupted previous run
  is dropped before the next attempt (handled by ``EDisclosureClient.download``).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urljoin, urlparse

from selectolax.parser import HTMLParser

from edx.http.client import EDisclosureClient
from edx.http.exceptions import RobotsDisallowedError, ScrapeFailedError
from edx.logging_setup import get_logger
from edx.storage import PublicationRow, PublicationsRepo

LINKED_SUBDIR: Final[str] = "linked"
DEFAULT_FILENAME: Final[str] = "main.bin"
HTML_CONTENT_HINTS: Final[tuple[str, ...]] = ("text/html", "application/xhtml")
LINKABLE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".pdf", ".rar", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt"}
)
SAFE_FILENAME_RE: Final[re.Pattern[str]] = re.compile(
    r"[^\w.\-Ѐ-ӿ]", re.UNICODE
)


@dataclass(frozen=True)
class DownloadedFile:
    relative_path: str
    sha256: str
    bytes: int
    content_type: str | None


@dataclass(frozen=True)
class DownloadOutcome:
    publication_id: str
    files: list[DownloadedFile]
    primary_hash: str
    skipped: bool


class DownloaderService:
    def __init__(
        self,
        client: EDisclosureClient,
        publications_repo: PublicationsRepo,
        *,
        raw_dir: Path,
        concurrency: int = 4,
        follow_html_links: bool = True,
        chunk_size_bytes: int = 64 * 1024,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.client = client
        self.publications_repo = publications_repo
        self.raw_dir = Path(raw_dir)
        self.concurrency = concurrency
        self.follow_html_links = follow_html_links
        self.chunk_size_bytes = chunk_size_bytes
        self._log = get_logger("edx.stages.downloader")

    async def run(
        self,
        publications: Iterable[PublicationRow],
    ) -> list[DownloadOutcome]:
        sem = asyncio.Semaphore(self.concurrency)
        outcomes: list[DownloadOutcome] = []
        pubs = list(publications)

        async def _one(pub: PublicationRow) -> DownloadOutcome | None:
            async with sem:
                try:
                    return await self._download_publication(pub)
                except RobotsDisallowedError as exc:
                    self._log.error(
                        "download_robots_disallowed",
                        publication_id=pub.publication_id,
                        url=exc.url,
                    )
                    self.publications_repo.mark_status(
                        pub.publication_id,
                        "failed",
                        error=f"robots disallowed: {exc.url}",
                    )
                    return None
                except ScrapeFailedError as exc:
                    self._log.error(
                        "download_failed",
                        publication_id=pub.publication_id,
                        error=str(exc),
                    )
                    self.publications_repo.mark_status(
                        pub.publication_id, "failed", error=str(exc)
                    )
                    return None
                except Exception as exc:  # noqa: BLE001 — surface and continue
                    self._log.error(
                        "download_unexpected_error",
                        publication_id=pub.publication_id,
                        error=str(exc),
                        exc_type=type(exc).__name__,
                    )
                    self.publications_repo.mark_status(
                        pub.publication_id, "failed", error=str(exc)
                    )
                    return None

        results = await asyncio.gather(*[_one(p) for p in pubs])
        for r in results:
            if r is not None:
                outcomes.append(r)
        return outcomes

    async def _download_publication(
        self, pub: PublicationRow
    ) -> DownloadOutcome:
        pub_dir = self.raw_dir / pub.ticker / pub.publication_id
        pub_dir.mkdir(parents=True, exist_ok=True)

        main_filename = filename_from_url(pub.source_url)
        main_path = pub_dir / main_filename

        if (
            pub.file_hash
            and main_path.exists()
            and _sha256_of_file(main_path) == pub.file_hash
        ):
            self._log.info(
                "download_skipped_identical_hash",
                publication_id=pub.publication_id,
                path=str(main_path),
                hash=pub.file_hash,
            )
            inventoried = self._inventory_existing(pub_dir, primary=main_path)
            # Bugfix: dedup-skip used to leave the publication in
            # ``discovered`` and the orchestrator's downstream stages
            # (Unpacker, Classifier, …) never picked it up. After
            # ``edx run --full-reload`` resets every status to
            # ``discovered``, the Downloader sees the file already on
            # disk with a matching hash, skips the HTTP fetch, and now
            # also advances the status so the rest of the pipeline runs.
            self.publications_repo.mark_status(
                pub.publication_id, "downloaded", file_hash=pub.file_hash
            )
            return DownloadOutcome(
                publication_id=pub.publication_id,
                files=inventoried,
                primary_hash=pub.file_hash,
                skipped=True,
            )

        download = await self.client.download(
            pub.source_url, main_path, chunk_size=self.chunk_size_bytes
        )
        files: list[DownloadedFile] = [
            DownloadedFile(
                relative_path=str(main_path.relative_to(pub_dir)),
                sha256=download.sha256,
                bytes=download.bytes_written,
                content_type=download.content_type,
            )
        ]

        if self.follow_html_links and _looks_like_html(
            download.content_type, main_path
        ):
            sub_dir = pub_dir / LINKED_SUBDIR
            for link_url in _extract_document_links(
                main_path.read_text(encoding="utf-8", errors="replace"),
                base_url=pub.source_url,
            ):
                sub_path = sub_dir / filename_from_url(
                    link_url, default="linked.bin"
                )
                try:
                    linked = await self.client.download(
                        link_url, sub_path, chunk_size=self.chunk_size_bytes
                    )
                except (ScrapeFailedError, RobotsDisallowedError) as exc:
                    self._log.warning(
                        "linked_download_failed",
                        publication_id=pub.publication_id,
                        url=link_url,
                        error=str(exc),
                    )
                    continue
                files.append(
                    DownloadedFile(
                        relative_path=str(sub_path.relative_to(pub_dir)),
                        sha256=linked.sha256,
                        bytes=linked.bytes_written,
                        content_type=linked.content_type,
                    )
                )

        self.publications_repo.mark_status(
            pub.publication_id, "downloaded", file_hash=download.sha256
        )
        self._log.info(
            "publication_downloaded",
            publication_id=pub.publication_id,
            file_count=len(files),
            primary_bytes=download.bytes_written,
        )
        return DownloadOutcome(
            publication_id=pub.publication_id,
            files=files,
            primary_hash=download.sha256,
            skipped=False,
        )

    def _inventory_existing(
        self, pub_dir: Path, *, primary: Path
    ) -> list[DownloadedFile]:
        out: list[DownloadedFile] = []
        for path in sorted(pub_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name.endswith(".partial"):
                continue
            out.append(
                DownloadedFile(
                    relative_path=str(path.relative_to(pub_dir)),
                    sha256=_sha256_of_file(path),
                    bytes=path.stat().st_size,
                    content_type=None,
                )
            )
        return out


def filename_from_url(url: str, *, default: str = DEFAULT_FILENAME) -> str:
    """Derive a safe filename from the last URL path segment.

    Trailing-slash URLs (``.../foo/``) and empty paths fall back to ``default``;
    we don't synthesise a name from a directory segment.
    """
    parsed = urlparse(url)
    raw_path = parsed.path
    if not raw_path or raw_path.endswith("/"):
        return default
    raw = unquote(Path(raw_path).name).strip()
    if not raw:
        return default
    sanitized = SAFE_FILENAME_RE.sub("_", raw)
    return sanitized or default


def _sha256_of_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _looks_like_html(
    content_type: str | None, path: Path
) -> bool:
    if content_type:
        ct = content_type.lower()
        if any(hint in ct for hint in HTML_CONTENT_HINTS):
            return True
    return path.suffix.lower() in {".html", ".htm"}


def _extract_document_links(html: str, *, base_url: str) -> Sequence[str]:
    """Find ``<a href>`` URLs that look like document downloads."""
    if not html:
        return []
    tree = HTMLParser(html)
    links: list[str] = []
    seen: set[str] = set()
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        path_suffix = Path(urlparse(absolute).path).suffix.lower()
        if path_suffix not in LINKABLE_SUFFIXES:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links
