"""TextExtractorService: dispatch native vs OCR vs hybrid, write JSON, update DB.

Patch 18 adds the hybrid path (native + partial OCR for the scanned pages
of an otherwise machine-readable PDF). Tests exercise three real-world
shapes — banking RSBU hybrid, corporate RSBU all-text, and bank annual
report all-text — to lock down both the new behaviour and the
"no OCR for pure-text PDFs" anti-regression.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pymupdf
import pytest

from edx.config import TickerEntry
from edx.stages.classifier.service import ClassifierService
from edx.stages.text_extractor.models import PageText
from edx.stages.text_extractor.service import TextExtractorService
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    PublicationsRepo,
    TickersRepo,
)

REAL_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pdf"


class _FakeOCRProvider:
    """Counts every recognise call. Used by anti-regression tests to assert
    the hybrid path doesn't OCR pure-text PDFs."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def recognize(
        self, pdf_path: Path, langs: list[str]
    ) -> list[PageText]:
        self.calls.append(pdf_path)
        # Pretend each page in the input PDF yields a deterministic text
        # so hybrid merging can be observed.
        with pymupdf.open(str(pdf_path)) as doc:  # type: ignore[no-untyped-call]
            page_count = doc.page_count
        return [
            PageText(
                page_number=i + 1,
                text=f"FAKE-OCR-TEXT page {i + 1} of {pdf_path.name}",
            )
            for i in range(page_count)
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


# --- Patch 18 — multi-issuer hybrid + anti-regression --------------------


def _seed_real_pdf_workspace(
    tmp_path: Path, src_pdf: Path, *, pub_id: str = "pub-real"
) -> tuple[Database, Path, Path, str]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    pub_dir = raw_dir / "SBER" / pub_id
    pub_dir.mkdir(parents=True)
    rel = "_unpacked/r.pdf"
    (pub_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (pub_dir / rel).write_bytes(src_pdf.read_bytes())

    with closing(db.connect()) as conn:
        TickersRepo(db, conn).upsert_from_config(
            [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
        )
        pubs = PublicationsRepo(db, conn)
        pubs.upsert_discovered(
            publication_id=pub_id,
            ticker="SBER",
            publication_type="report",
            publication_date="2026-04-01",
            source_url="https://example.test/r.zip",
        )
        for status in ("downloaded", "unpacked"):
            pubs.mark_status(pub_id, status)  # type: ignore[arg-type]
        DocumentsRepo(db, conn).add_documents(
            pub_id,
            [
                DocumentInput(
                    relative_path=rel,
                    file_hash="real-h",
                    mime_type="application/pdf",
                )
            ],
        )
    return db, raw_dir, processed_dir, pub_id


def _classify_then_extract(
    db: Database,
    raw_dir: Path,
    processed_dir: Path,
    pub_id: str,
    fake_ocr: _FakeOCRProvider,
) -> dict[str, object]:
    """Run Classifier and TextExtractor end-to-end, return outcome+payload."""
    from edx.config import MetricsConfig, MetricSpec, MetricsProfile

    metrics_config = MetricsConfig(
        profiles={
            "non_bank": MetricsProfile(
                metrics={"revenue": MetricSpec(synonyms=["Выручка"])},
                reporting_priority=["IFRS", "RSBU"],
            ),
            "bank": MetricsProfile(
                metrics={
                    "net_interest_income": MetricSpec(
                        synonyms=["Чистый процентный доход"]
                    )
                },
                reporting_priority=["IFRS", "RSBU"],
            ),
        }
    )

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        ClassifierService(
            publications_repo,
            documents_repo,
            raw_dir=raw_dir,
            metrics_config=metrics_config,
            min_text_chars_per_page=50,
            first_pages_to_inspect=3,
        ).run([publications_repo.get_by_id(pub_id)])  # type: ignore[list-item]

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        outcomes = TextExtractorService(
            publications_repo,
            documents_repo,
            fake_ocr,
            raw_dir=raw_dir,
            processed_dir=processed_dir,
            ocr_langs=["eng"],
            extract_tables_enabled=False,
        ).run([publications_repo.get_by_id(pub_id)])  # type: ignore[list-item]
        docs = documents_repo.list_for_publication(pub_id)
    extract_path = docs[0].text_extract_path
    payload = json.loads((processed_dir / (extract_path or "")).read_text("utf-8"))
    return {"outcome": outcomes[0], "payload": payload}


def test_hybrid_extractor_ocrs_only_scan_pages_in_sber_rpbu(
    tmp_path: Path,
) -> None:
    """SBER RSBU 9M 2025: pages 0–3 native, 4–16 routed through OCR exactly once."""
    db, raw_dir, processed_dir, pub_id = _seed_real_pdf_workspace(
        tmp_path, REAL_FIXTURES / "sber_rpbu_9m2025.pdf"
    )
    fake_ocr = _FakeOCRProvider()
    result = _classify_then_extract(db, raw_dir, processed_dir, pub_id, fake_ocr)

    outcome = result["outcome"]
    assert outcome.documents_processed == 1  # type: ignore[attr-defined]
    assert outcome.native_count == 1  # type: ignore[attr-defined]
    assert outcome.hybrid_count == 1  # type: ignore[attr-defined]
    assert outcome.ocr_count == 0  # type: ignore[attr-defined]

    # OCR called exactly once — on the temp sub-PDF carrying scan pages.
    # The temp file is deleted by the extractor before this assertion runs,
    # so we verify *via the merged payload* that 13 pages got OCR text and
    # 4 pages stayed native — not by reopening the deleted temp PDF.
    assert len(fake_ocr.calls) == 1

    payload = result["payload"]
    assert payload["extraction_method"] == "native+ocr_fake"  # type: ignore[index]
    pages = payload["pages"]  # type: ignore[index]
    assert len(pages) == 17
    ocr_pages = [p for p in pages if "FAKE-OCR-TEXT" in p["text"]]
    native_pages = [p for p in pages if "FAKE-OCR-TEXT" not in p["text"]]
    # Native should still own pages 1..4 (page_number is 1-based).
    assert {p["page_number"] for p in native_pages} == {1, 2, 3, 4}
    # OCR should have filled pages 5..17.
    assert {p["page_number"] for p in ocr_pages} == set(range(5, 18))


def test_hybrid_extractor_no_ocr_call_on_lkoh_rsbu_all_text(
    tmp_path: Path,
) -> None:
    """Anti-regression: pure-text corporate RSBU must not invoke OCR."""
    db, raw_dir, processed_dir, pub_id = _seed_real_pdf_workspace(
        tmp_path, REAL_FIXTURES / "lkoh_rsbu_q1_2026.pdf"
    )
    fake_ocr = _FakeOCRProvider()
    result = _classify_then_extract(db, raw_dir, processed_dir, pub_id, fake_ocr)
    outcome = result["outcome"]
    assert outcome.native_count == 1  # type: ignore[attr-defined]
    assert outcome.hybrid_count == 0  # type: ignore[attr-defined]
    assert outcome.ocr_count == 0  # type: ignore[attr-defined]
    assert fake_ocr.calls == []
    payload = result["payload"]
    assert payload["extraction_method"] == "native"  # type: ignore[index]


def test_hybrid_extractor_no_ocr_call_on_vtb_go_all_text(
    tmp_path: Path,
) -> None:
    """Anti-regression on a different banking issuer's pure-text annual report."""
    db, raw_dir, processed_dir, pub_id = _seed_real_pdf_workspace(
        tmp_path, REAL_FIXTURES / "vtb_go_2024_first30.pdf"
    )
    fake_ocr = _FakeOCRProvider()
    result = _classify_then_extract(db, raw_dir, processed_dir, pub_id, fake_ocr)
    outcome = result["outcome"]
    assert outcome.native_count == 1  # type: ignore[attr-defined]
    assert outcome.hybrid_count == 0  # type: ignore[attr-defined]
    assert outcome.ocr_count == 0  # type: ignore[attr-defined]
    assert fake_ocr.calls == []


def test_hybrid_extractor_no_ocr_partial_log_on_pure_text_doc(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ``text_extractor_ocr_partial`` event fires only on hybrids."""
    db, raw_dir, processed_dir, pub_id = _seed_real_pdf_workspace(
        tmp_path, REAL_FIXTURES / "lkoh_rsbu_q1_2026.pdf"
    )
    fake_ocr = _FakeOCRProvider()
    with caplog.at_level("INFO"):
        _classify_then_extract(db, raw_dir, processed_dir, pub_id, fake_ocr)
    assert not any(
        "text_extractor_ocr_partial" in r.getMessage() for r in caplog.records
    )


def test_hybrid_extractor_emits_ocr_partial_log_on_sber_rpbu(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db, raw_dir, processed_dir, pub_id = _seed_real_pdf_workspace(
        tmp_path, REAL_FIXTURES / "sber_rpbu_9m2025.pdf"
    )
    fake_ocr = _FakeOCRProvider()
    with caplog.at_level("INFO"):
        _classify_then_extract(db, raw_dir, processed_dir, pub_id, fake_ocr)
    assert any(
        "text_extractor_ocr_partial" in r.getMessage() for r in caplog.records
    )
