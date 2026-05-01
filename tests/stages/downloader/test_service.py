"""DownloaderService unit tests against MockTransport."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from edx.config import TickerEntry
from edx.http.client import EDisclosureClient
from edx.stages.downloader.service import DownloaderService, filename_from_url
from edx.storage import Database, PublicationsRepo, TickersRepo


def _seed_publication(
    tmp_path: Path, source_url: str
) -> tuple[Database, str]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    conn = db.connect()
    try:
        TickersRepo(db, conn).upsert_from_config(
            [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
        )
        PublicationsRepo(db, conn).upsert_discovered(
            publication_id="pub-1",
            ticker="SBER",
            publication_type="report",
            publication_date="2026-04-01",
            source_url=source_url,
        )
    finally:
        conn.close()
    return db, "pub-1"


def _binary_transport(payload: bytes, content_type: str = "application/pdf") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(
            200,
            content=payload,
            headers={"Content-Type": content_type},
        )

    return httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport) -> EDisclosureClient:
    return EDisclosureClient(
        base_url="https://example.test",
        user_agent="edx-test/1.0",
        requests_per_second=100.0,
        max_retries=0,
        retry_min_wait_s=0.0,
        retry_max_wait_s=0.01,
        respect_robots=False,
        transport=transport,
    )


def _service(
    client: EDisclosureClient,
    db: Database,
    *,
    raw_dir: Path,
    follow_html_links: bool = True,
) -> tuple[DownloaderService, PublicationsRepo, object]:
    conn = db.connect()
    repo = PublicationsRepo(db, conn)
    service = DownloaderService(
        client,
        repo,
        raw_dir=raw_dir,
        concurrency=2,
        follow_html_links=follow_html_links,
        chunk_size_bytes=8,
    )
    return service, repo, conn


@pytest.mark.asyncio
async def test_first_download_writes_file_and_hash(tmp_path: Path) -> None:
    payload = b"PDFBYTES" * 100
    db, pub_id = _seed_publication(tmp_path, "https://example.test/files/r.pdf")
    raw_dir = tmp_path / "raw"

    async with _client(_binary_transport(payload)) as client:
        service, repo, conn = _service(client, db, raw_dir=raw_dir)
        try:
            pub = repo.get_by_id(pub_id)
            assert pub is not None
            outcomes = await service.run([pub])
        finally:
            conn.close()

    assert len(outcomes) == 1
    out = outcomes[0]
    assert not out.skipped
    target = raw_dir / "SBER" / pub_id / "r.pdf"
    assert target.exists()
    assert target.read_bytes() == payload
    assert out.primary_hash == hashlib.sha256(payload).hexdigest()

    with closing(db.connect()) as conn:
        row = PublicationsRepo(db, conn).get_by_id(pub_id)
    assert row is not None
    assert row.status == "downloaded"
    assert row.file_hash == out.primary_hash


@pytest.mark.asyncio
async def test_second_run_with_identical_hash_is_skipped(tmp_path: Path) -> None:
    payload = b"identical-content"
    db, pub_id = _seed_publication(
        tmp_path, "https://example.test/files/a.pdf"
    )
    raw_dir = tmp_path / "raw"

    transport = _binary_transport(payload)

    # First pass — actual download.
    async with _client(transport) as client:
        service, repo, conn = _service(client, db, raw_dir=raw_dir)
        try:
            pub = repo.get_by_id(pub_id)
            assert pub is not None
            await service.run([pub])
        finally:
            conn.close()

    # Reset publication status to 'discovered' to simulate a re-run while
    # keeping file_hash on disk and in the DB.
    with closing(db.connect()) as conn:
        conn.execute(
            "UPDATE publications SET status = 'discovered' WHERE publication_id = ?",
            (pub_id,),
        )

    # A transport that would explode if called: forces "must be skipped".
    def exploding_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        raise AssertionError("downloader must skip identical-hash publication")

    async with _client(httpx.MockTransport(exploding_handler)) as client:
        service, repo, conn = _service(client, db, raw_dir=raw_dir)
        try:
            pub = repo.get_by_id(pub_id)
            assert pub is not None
            outcomes = await service.run([pub])
        finally:
            conn.close()

    assert len(outcomes) == 1
    assert outcomes[0].skipped is True


@pytest.mark.asyncio
async def test_partial_download_cleanup_on_stream_failure(
    tmp_path: Path,
) -> None:
    """Mid-stream failure must not leave a *.partial in the publication dir."""

    class FailingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.iters = 0

        async def __aiter__(self) -> Iterator[bytes]:  # type: ignore[override]
            self.iters += 1
            yield b"first-chunk"
            raise httpx.ReadError("simulated mid-stream failure")

        async def aclose(self) -> None:
            return None

    fail_stream = FailingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(
            200, stream=fail_stream, headers={"Content-Type": "application/pdf"}
        )

    transport = httpx.MockTransport(handler)
    db, pub_id = _seed_publication(
        tmp_path, "https://example.test/files/big.pdf"
    )
    raw_dir = tmp_path / "raw"

    async with _client(transport) as client:
        service, repo, conn = _service(client, db, raw_dir=raw_dir)
        try:
            pub = repo.get_by_id(pub_id)
            assert pub is not None
            await service.run([pub])
        finally:
            conn.close()

    pub_dir = raw_dir / "SBER" / pub_id
    if pub_dir.exists():
        leftovers = [p.name for p in pub_dir.iterdir()]
        assert all(not name.endswith(".partial") for name in leftovers), leftovers
        assert "big.pdf" not in leftovers, leftovers

    with closing(db.connect()) as conn:
        row = PublicationsRepo(db, conn).get_by_id(pub_id)
    assert row is not None
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_html_with_links_downloads_referenced_files(tmp_path: Path) -> None:
    html = (
        b"<html><body>"
        b"<a href='/files/report.pdf'>Report</a>"
        b"<a href='/files/notes.pdf'>Notes</a>"
        b"<a href='https://other.test/skip.txt'>SKIP</a>"
        b"<a href='#section'>SKIP-anchor</a>"
        b"</body></html>"
    )
    pdf_a = b"PDF-A" * 100
    pdf_b = b"PDF-B" * 200

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        path = request.url.path
        if path.endswith("/index.html"):
            return httpx.Response(
                200, content=html, headers={"Content-Type": "text/html"}
            )
        if path.endswith("report.pdf"):
            return httpx.Response(
                200, content=pdf_a, headers={"Content-Type": "application/pdf"}
            )
        if path.endswith("notes.pdf"):
            return httpx.Response(
                200, content=pdf_b, headers={"Content-Type": "application/pdf"}
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    db, pub_id = _seed_publication(
        tmp_path, "https://example.test/events/index.html"
    )
    raw_dir = tmp_path / "raw"

    async with _client(transport) as client:
        service, repo, conn = _service(client, db, raw_dir=raw_dir)
        try:
            pub = repo.get_by_id(pub_id)
            assert pub is not None
            outcomes = await service.run([pub])
        finally:
            conn.close()

    assert len(outcomes) == 1
    rels = {f.relative_path for f in outcomes[0].files}
    assert "index.html" in rels
    assert any(r.endswith("report.pdf") for r in rels)
    assert any(r.endswith("notes.pdf") for r in rels)

    pub_dir = raw_dir / "SBER" / pub_id
    assert (pub_dir / "linked" / "report.pdf").exists()
    assert (pub_dir / "linked" / "notes.pdf").exists()


def test_filename_from_url_handles_edge_cases() -> None:
    assert filename_from_url("https://x.test/a/b/file.pdf") == "file.pdf"
    assert filename_from_url("https://x.test/?id=42") == "main.bin"
    assert filename_from_url("https://x.test/") == "main.bin"
    assert filename_from_url("https://x.test/a/", default="d.bin") == "d.bin"
    # Cyrillic is preserved (URL-decoded).
    assert "отчет" in filename_from_url("https://x.test/%D0%BE%D1%82%D1%87%D0%B5%D1%82.pdf")
    # Disallowed chars are replaced with underscore.
    assert filename_from_url("https://x.test/file%20with%20spaces.pdf") == "file_with_spaces.pdf"
