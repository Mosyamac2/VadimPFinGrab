"""TextExtractorService: dispatch native vs OCR, write JSON, update DB."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from edx.config import TickerEntry
from edx.stages.text_extractor.models import PageText
from edx.stages.text_extractor.service import TextExtractorService
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    PublicationsRepo,
    TickersRepo,
)


class _FakeOCRProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def recognize(
        self, pdf_path: Path, langs: list[str]
    ) -> list[PageText]:
        self.calls.append(pdf_path)
        return [
            PageText(
                page_number=1,
                text=f"FAKE-OCR-TEXT for {pdf_path.name}",
            )
        ]


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[Database, Path, Path]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    pub_dir = raw_dir / "SBER" / "pub-1"
    pub_dir.mkdir(parents=True)
    with closing(db.connect()) as conn:
        TickersRepo(db, conn).upsert_from_config(
            [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
        )
        pubs = PublicationsRepo(db, conn)
        pubs.upsert_discovered(
            publication_id="pub-1",
            ticker="SBER",
            publication_type="report",
            publication_date="2026-04-01",
            source_url="https://example.test/r.zip",
        )
        for status in ("downloaded", "unpacked", "classified"):
            pubs.mark_status("pub-1", status)  # type: ignore[arg-type]
    return db, raw_dir, processed_dir


def test_service_writes_native_and_ocr_extracts(
    seeded: tuple[Database, Path, Path],
    make_text_pdf: Callable[[Path, str], Path],
    make_scan_pdf: Callable[[Path], Path],
) -> None:
    db, raw_dir, processed_dir = seeded
    pub_dir = raw_dir / "SBER" / "pub-1"

    text_rel = "_unpacked/report.pdf"
    scan_rel = "_unpacked/scan.pdf"
    body = "IFRS consolidated financial statements 2025\nBalance Sheet\n" * 10
    make_text_pdf(pub_dir / text_rel, body)
    make_scan_pdf(pub_dir / scan_rel)

    with closing(db.connect()) as conn:
        documents_repo = DocumentsRepo(db, conn)
        documents_repo.add_documents(
            "pub-1",
            [
                DocumentInput(
                    relative_path=text_rel,
                    file_hash="h1",
                    mime_type="application/pdf",
                ),
                DocumentInput(
                    relative_path=scan_rel,
                    file_hash="h2",
                    mime_type="application/pdf",
                ),
            ],
        )
        docs = documents_repo.list_for_publication("pub-1")
        text_doc = next(d for d in docs if d.relative_path == text_rel)
        scan_doc = next(d for d in docs if d.relative_path == scan_rel)
        documents_repo.update_classification(
            text_doc.document_id,
            reporting_standard="IFRS",
            report_form="balance_sheet",
            is_machine_readable=True,
            page_count=1,
        )
        documents_repo.update_classification(
            scan_doc.document_id,
            reporting_standard="OTHER",
            report_form="other",
            is_machine_readable=False,
            page_count=1,
        )

    fake_ocr = _FakeOCRProvider()

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = TextExtractorService(
            publications_repo,
            documents_repo,
            fake_ocr,
            raw_dir=raw_dir,
            processed_dir=processed_dir,
            ocr_langs=["eng"],
            max_chars=400_000,
            extract_tables_enabled=False,  # speed up: skip pdfplumber
        )
        pub = publications_repo.get_by_id("pub-1")
        assert pub is not None
        outcomes = service.run([pub])
        docs = documents_repo.list_for_publication("pub-1")
        pub_after = publications_repo.get_by_id("pub-1")

    assert len(outcomes) == 1
    out = outcomes[0]
    assert out.documents_processed == 2
    assert out.native_count == 1
    assert out.ocr_count == 1
    assert pub_after is not None and pub_after.status == "extracted"

    by_id = {d.document_id: d for d in docs}
    text_extract_path = by_id[text_doc.document_id].text_extract_path
    scan_extract_path = by_id[scan_doc.document_id].text_extract_path
    assert text_extract_path is not None
    assert scan_extract_path is not None

    text_payload = json.loads(
        (processed_dir / text_extract_path).read_text(encoding="utf-8")
    )
    assert text_payload["extraction_method"] == "native"
    assert text_payload["pages"][0]["page_number"] == 1
    assert "IFRS" in text_payload["pages"][0]["text"]

    scan_payload = json.loads(
        (processed_dir / scan_extract_path).read_text(encoding="utf-8")
    )
    assert scan_payload["extraction_method"] == "ocr_fake"
    assert "FAKE-OCR-TEXT" in scan_payload["pages"][0]["text"]
    # OCR provider was called exactly once with the scan's path.
    assert len(fake_ocr.calls) == 1
    assert fake_ocr.calls[0].name == "scan.pdf"


def test_service_skips_unclassified_pdfs(
    seeded: tuple[Database, Path, Path],
    make_text_pdf: Callable[[Path, str], Path],
) -> None:
    db, raw_dir, processed_dir = seeded
    pub_dir = raw_dir / "SBER" / "pub-1"
    rel = "doc.pdf"
    make_text_pdf(pub_dir / rel, "Some text")

    with closing(db.connect()) as conn:
        DocumentsRepo(db, conn).add_documents(
            "pub-1",
            [DocumentInput(relative_path=rel, file_hash="h", mime_type="application/pdf")],
        )

    fake_ocr = _FakeOCRProvider()

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = TextExtractorService(
            publications_repo,
            documents_repo,
            fake_ocr,
            raw_dir=raw_dir,
            processed_dir=processed_dir,
            ocr_langs=["eng"],
            extract_tables_enabled=False,
        )
        pub = publications_repo.get_by_id("pub-1")
        assert pub is not None
        outcomes = service.run([pub])
        docs = documents_repo.list_for_publication("pub-1")

    # is_machine_readable was never set → service skips this doc.
    assert outcomes[0].documents_processed == 0
    assert docs[0].text_extract_path is None


def test_service_truncates_when_exceeding_max_chars(
    seeded: tuple[Database, Path, Path],
    make_text_pdf: Callable[[Path, str], Path],
) -> None:
    db, raw_dir, processed_dir = seeded
    pub_dir = raw_dir / "SBER" / "pub-1"
    rel = "long.pdf"
    # 30 lines of 50 chars each → ~1500 chars after extraction; PyMuPDF
    # preserves separate insert_text calls so this survives the renderer.
    body = "\n".join("A" * 50 for _ in range(30))
    make_text_pdf(pub_dir / rel, body)

    with closing(db.connect()) as conn:
        documents_repo = DocumentsRepo(db, conn)
        documents_repo.add_documents(
            "pub-1",
            [DocumentInput(relative_path=rel, file_hash="h", mime_type="application/pdf")],
        )
        doc_id = documents_repo.list_for_publication("pub-1")[0].document_id
        documents_repo.update_classification(
            doc_id,
            reporting_standard="OTHER",
            report_form="other",
            is_machine_readable=True,
            page_count=1,
        )

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = TextExtractorService(
            publications_repo,
            documents_repo,
            _FakeOCRProvider(),
            raw_dir=raw_dir,
            processed_dir=processed_dir,
            ocr_langs=["eng"],
            max_chars=100,
            extract_tables_enabled=False,
        )
        pub = publications_repo.get_by_id("pub-1")
        assert pub is not None
        outcomes = service.run([pub])

    assert outcomes[0].truncated is True
    # And no more than max_chars survived.
    assert outcomes[0].total_chars <= 100
