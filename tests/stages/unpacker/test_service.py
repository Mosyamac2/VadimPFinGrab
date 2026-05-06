"""UnpackerService tests: ZIP extraction, traversal guard, RAR (skipped if no unrar)."""

from __future__ import annotations

import shutil
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from edx.config import TickerEntry
from edx.stages.unpacker.service import (
    UnpackerError,
    UnpackerService,
)
from edx.storage import (
    Database,
    DocumentsRepo,
    PublicationsRepo,
    TickersRepo,
)


def _seed_publication(
    tmp_path: Path,
    *,
    pub_id: str = "pub-1",
    ticker: str = "SBER",
) -> tuple[Database, sqlite3.Connection, Path]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    conn = db.connect()
    TickersRepo(db, conn).upsert_from_config(
        [TickerEntry(ticker=ticker, e_disclosure_id="1", name=ticker)]
    )
    PublicationsRepo(db, conn).upsert_discovered(
        publication_id=pub_id,
        ticker=ticker,
        publication_type="report",
        publication_date="2026-04-01",
        source_url="https://example.test/file.zip",
    )
    PublicationsRepo(db, conn).mark_status(pub_id, "downloaded", file_hash="x")
    pub_dir = tmp_path / "raw" / ticker / pub_id
    pub_dir.mkdir(parents=True, exist_ok=True)
    return db, conn, pub_dir


def _service(
    db: Database,
    conn: sqlite3.Connection,
    *,
    raw_dir: Path,
    max_unpacked_mb: int = 500,
) -> UnpackerService:
    return UnpackerService(
        db=db,
        publications_repo=PublicationsRepo(db, conn),
        documents_repo=DocumentsRepo(db, conn),
        raw_dir=raw_dir,
        max_unpacked_mb=max_unpacked_mb,
    )


def test_zip_detected_by_magic_bytes_when_extension_missing(
    tmp_path: Path,
) -> None:
    """Real-world e-disclosure case: ``FileLoad.ashx`` body is a ZIP but
    the filename has no ``.zip`` suffix. Magic-byte sniffing should still
    extract it; otherwise the publication silently goes to Classifier
    with zero PDFs and the whole pipeline skips it (saw this in prod)."""
    db, conn, pub_dir = _seed_publication(tmp_path)
    raw_dir = tmp_path / "raw"

    # Filename matches what live Downloader writes for FileLoad.ashx URLs.
    archive = pub_dir / "FileLoad.ashx"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("report.pdf", b"PDF-BYTES")

    try:
        service = _service(db, conn, raw_dir=raw_dir)
        pub = PublicationsRepo(db, conn).get_by_id("pub-1")
        assert pub is not None
        outcomes = service.run([pub])
    finally:
        conn.close()

    assert outcomes[0].archives_extracted == 1
    assert outcomes[0].documents_added == 1
    assert (pub_dir / "_unpacked" / "report.pdf").read_bytes() == b"PDF-BYTES"


def test_non_archive_file_without_extension_is_inventoried_not_extracted(
    tmp_path: Path,
) -> None:
    """A non-archive payload (HTML, text, JSON) that happens to have no
    extension shouldn't be force-decoded as ZIP — it goes into the
    documents inventory as-is."""
    db, conn, pub_dir = _seed_publication(tmp_path)
    raw_dir = tmp_path / "raw"
    (pub_dir / "FileLoad.ashx").write_bytes(b"<html>not a zip</html>")

    try:
        service = _service(db, conn, raw_dir=raw_dir)
        pub = PublicationsRepo(db, conn).get_by_id("pub-1")
        assert pub is not None
        outcomes = service.run([pub])
    finally:
        conn.close()

    assert outcomes[0].archives_extracted == 0
    assert outcomes[0].documents_added == 1


def test_zip_extracted_and_documents_inventoried(tmp_path: Path) -> None:
    db, conn, pub_dir = _seed_publication(tmp_path)
    raw_dir = tmp_path / "raw"

    archive = pub_dir / "report.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("balance.pdf", b"BALANCE-CONTENT")
        zf.writestr("notes/details.pdf", b"DETAILS-CONTENT")

    try:
        service = _service(db, conn, raw_dir=raw_dir)
        publications_repo = PublicationsRepo(db, conn)
        pub = publications_repo.get_by_id("pub-1")
        assert pub is not None
        outcomes = service.run([pub])
    finally:
        conn.close()

    assert len(outcomes) == 1
    assert outcomes[0].archives_extracted == 1
    assert outcomes[0].documents_added == 2

    assert (pub_dir / "_unpacked" / "balance.pdf").read_bytes() == b"BALANCE-CONTENT"
    assert (
        pub_dir / "_unpacked" / "notes" / "details.pdf"
    ).read_bytes() == b"DETAILS-CONTENT"

    with closing(db.connect()) as fresh_conn:
        repo = DocumentsRepo(db, fresh_conn)
        docs = repo.list_for_publication("pub-1")
        rels = sorted(d.relative_path for d in docs)
    expected_relatives = sorted(
        [
            "_unpacked/balance.pdf",
            "_unpacked/notes/details.pdf",
        ]
    )
    assert rels == expected_relatives
    publication = PublicationsRepo(
        db, db.connect()
    )  # quick sanity check on status
    pub_row = publication.get_by_id("pub-1")
    assert pub_row is not None and pub_row.status == "unpacked"


def test_zip_with_path_traversal_marks_failed(tmp_path: Path) -> None:
    db, conn, pub_dir = _seed_publication(tmp_path)
    raw_dir = tmp_path / "raw"

    archive = pub_dir / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../etc/passwd", b"BAD")
        zf.writestr("normal.pdf", b"OK")

    try:
        service = _service(db, conn, raw_dir=raw_dir)
        repo = PublicationsRepo(db, conn)
        pub = repo.get_by_id("pub-1")
        assert pub is not None
        service.run([pub])
        pub_after = repo.get_by_id("pub-1")
    finally:
        conn.close()

    # Nothing should be written outside the publication directory.
    assert not (tmp_path / "etc" / "passwd").exists()
    assert not (tmp_path.parent / "etc" / "passwd").exists()
    # Status flipped to failed and the partial _unpacked dir is gone.
    assert pub_after is not None and pub_after.status == "failed"
    assert pub_after.last_error is not None
    assert "traversal" in pub_after.last_error
    assert not (pub_dir / "_unpacked").exists()


def test_zip_size_limit_enforced(tmp_path: Path) -> None:
    db, conn, pub_dir = _seed_publication(tmp_path)
    raw_dir = tmp_path / "raw"

    archive = pub_dir / "big.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        # Two members of 1 MiB each — a 1 MiB cap allows the first but not both.
        zf.writestr("a.bin", b"A" * (1024 * 1024))
        zf.writestr("b.bin", b"B" * (1024 * 1024))

    try:
        service = _service(db, conn, raw_dir=raw_dir, max_unpacked_mb=1)
        repo = PublicationsRepo(db, conn)
        pub = repo.get_by_id("pub-1")
        assert pub is not None
        service.run([pub])
        pub_after = repo.get_by_id("pub-1")
    finally:
        conn.close()

    assert pub_after is not None and pub_after.status == "failed"
    assert pub_after.last_error is not None
    assert "MB cap" in pub_after.last_error


def test_no_archive_inventories_existing_files(tmp_path: Path) -> None:
    db, conn, pub_dir = _seed_publication(tmp_path)
    raw_dir = tmp_path / "raw"

    # Simulate a directly-downloaded HTML + PDF for an event publication.
    (pub_dir / "index.html").write_bytes(b"<html>...</html>")
    linked_dir = pub_dir / "linked"
    linked_dir.mkdir()
    (linked_dir / "doc.pdf").write_bytes(b"PDF-CONTENT")

    try:
        service = _service(db, conn, raw_dir=raw_dir)
        repo = PublicationsRepo(db, conn)
        pub = repo.get_by_id("pub-1")
        assert pub is not None
        outcomes = service.run([pub])
    finally:
        conn.close()

    assert len(outcomes) == 1
    assert outcomes[0].archives_extracted == 0
    assert outcomes[0].documents_added == 2

    with closing(db.connect()) as fresh_conn:
        rels = sorted(
            d.relative_path
            for d in DocumentsRepo(db, fresh_conn).list_for_publication("pub-1")
        )
    assert rels == ["index.html", "linked/doc.pdf"]


def test_validate_member_path_rejects_absolute(tmp_path: Path) -> None:
    db, conn, pub_dir = _seed_publication(tmp_path)
    try:
        service = _service(db, conn, raw_dir=tmp_path / "raw")
        with pytest.raises(UnpackerError, match="absolute path"):
            service._validate_member_path(
                "/etc/shadow", pub_dir / "x.zip"
            )
    finally:
        conn.close()


def test_corrupted_zip_marks_publication_failed_does_not_crash_stage(
    tmp_path: Path,
) -> None:
    """BadZipFile (bad CRC-32 or truncated archive from e-disclosure.ru) must
    mark the one publication as 'failed', not abort the whole unpacker stage.
    The second publication in the same run must still be processed."""
    db, conn, pub_dir = _seed_publication(tmp_path, pub_id="pub-bad")
    raw_dir = tmp_path / "raw"

    # Seed a second, healthy publication so we can verify the stage continues.
    TickersRepo(db, conn).upsert_from_config(
        [TickerEntry(ticker="SBER", e_disclosure_id="1", name="SBER")]
    )
    PublicationsRepo(db, conn).upsert_discovered(
        publication_id="pub-good",
        ticker="SBER",
        publication_type="report",
        publication_date="2026-04-02",
        source_url="https://example.test/good.zip",
    )
    PublicationsRepo(db, conn).mark_status("pub-good", "downloaded", file_hash="y")
    good_dir = raw_dir / "SBER" / "pub-good"
    good_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(good_dir / "good.zip", "w") as zf:
        zf.writestr("report.pdf", b"PDF-BYTES")

    # Write a corrupt ZIP (valid magic bytes, garbage body → BadZipFile on open).
    (pub_dir / "corrupt.zip").write_bytes(b"PK\x03\x04" + b"\xff" * 50)

    try:
        service = _service(db, conn, raw_dir=raw_dir)
        repo = PublicationsRepo(db, conn)
        bad_pub = repo.get_by_id("pub-bad")
        good_pub = repo.get_by_id("pub-good")
        assert bad_pub is not None and good_pub is not None
        outcomes = service.run([bad_pub, good_pub])
        bad_after = repo.get_by_id("pub-bad")
        good_after = repo.get_by_id("pub-good")
    finally:
        conn.close()

    assert bad_after is not None and bad_after.status == "failed"
    assert bad_after.last_error is not None and "zip extraction failed" in bad_after.last_error
    assert good_after is not None and good_after.status == "unpacked"
    assert len(outcomes) == 2
    assert outcomes[1].documents_added == 1


def test_zip_with_filename_too_long_marks_failed_not_crash_stage(
    tmp_path: Path,
) -> None:
    """ZIP member whose filename component exceeds the OS NAME_MAX (255 bytes on
    Linux/ext4/tmpfs) must mark the one publication as 'failed', not abort the
    entire unpacker stage.  The next publication in the same run must still be
    processed.  Regression test for the OSError: [Errno 36] File name too long
    crash that caused orchestrator_stage_failed on EDX1285-4-1265908 (tick #87)."""
    db, conn, pub_dir = _seed_publication(tmp_path, pub_id="pub-long")
    raw_dir = tmp_path / "raw"

    # Seed a second, healthy publication to verify the stage continues.
    TickersRepo(db, conn).upsert_from_config(
        [TickerEntry(ticker="SBER", e_disclosure_id="1", name="SBER")]
    )
    PublicationsRepo(db, conn).upsert_discovered(
        publication_id="pub-ok",
        ticker="SBER",
        publication_type="report",
        publication_date="2026-04-02",
        source_url="https://example.test/ok.zip",
    )
    PublicationsRepo(db, conn).mark_status("pub-ok", "downloaded", file_hash="y")
    ok_dir = raw_dir / "SBER" / "pub-ok"
    ok_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ok_dir / "ok.zip", "w") as zf:
        zf.writestr("report.pdf", b"PDF-BYTES")

    # Cyrillic 'а' (U+0430) is 2 bytes in UTF-8; 200 × 2 = 400 bytes > NAME_MAX=255.
    long_name = "а" * 200 + ".pdf"
    archive = pub_dir / "report.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(long_name, b"CONTENT")

    try:
        service = _service(db, conn, raw_dir=raw_dir)
        repo = PublicationsRepo(db, conn)
        bad_pub = repo.get_by_id("pub-long")
        ok_pub = repo.get_by_id("pub-ok")
        assert bad_pub is not None and ok_pub is not None
        outcomes = service.run([bad_pub, ok_pub])
        bad_after = repo.get_by_id("pub-long")
        ok_after = repo.get_by_id("pub-ok")
    finally:
        conn.close()

    assert bad_after is not None and bad_after.status == "failed"
    assert bad_after.last_error is not None and "zip extraction failed" in bad_after.last_error
    assert ok_after is not None and ok_after.status == "unpacked"
    assert len(outcomes) == 2
    assert outcomes[1].documents_added == 1


@pytest.mark.skipif(
    shutil.which("unrar") is None and shutil.which("unar") is None,
    reason="no unrar/unar binary on PATH",
)
def test_rar_extraction_when_unrar_available(tmp_path: Path) -> None:
    """Best-effort RAR test. Skipped on hosts without unrar (CI by default)."""
    rarfile = pytest.importorskip("rarfile")
    # Building a real RAR archive in-process requires rar binaries that are
    # rarely available. We fall back to verifying the sad path: a non-RAR file
    # with a .rar suffix should fail with UnpackerError, not crash the stage.
    db, conn, pub_dir = _seed_publication(tmp_path)
    archive = pub_dir / "broken.rar"
    archive.write_bytes(b"not actually a rar archive")
    try:
        service = _service(db, conn, raw_dir=tmp_path / "raw")
        repo = PublicationsRepo(db, conn)
        pub = repo.get_by_id("pub-1")
        assert pub is not None
        service.run([pub])
        pub_after = repo.get_by_id("pub-1")
    finally:
        conn.close()
    assert pub_after is not None and pub_after.status == "failed"
    # Confirms the rarfile dependency is loadable.
    assert hasattr(rarfile, "RarFile")
