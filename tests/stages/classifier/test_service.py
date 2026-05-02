"""ClassifierService end-to-end against synthetic and real-issuer PDFs."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from edx.config import (
    MetricsConfig,
    MetricSpec,
    MetricsProfile,
    TickerEntry,
)
from edx.stages.classifier.service import ClassifierService
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    PublicationsRepo,
    TickersRepo,
)

REAL_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pdf"

# Minimal Patch-19-shaped config — Classifier only uses it to score the
# reporting standard via heuristics, so the contents don't matter beyond
# satisfying the schema.
_METRICS_CONFIG = MetricsConfig(
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


@pytest.fixture
def seeded(
    tmp_path: Path,
) -> tuple[Database, Path, str]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    raw_dir = tmp_path / "raw"
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
        for status in ("downloaded", "unpacked"):
            pubs.mark_status("pub-1", status)  # type: ignore[arg-type]
    return db, raw_dir, "pub-1"


def test_classifies_machine_readable_ifrs_pdf(
    tmp_path: Path,
    make_text_pdf: Callable[[Path, str], Path],
    seeded: tuple[Database, Path, str],
) -> None:
    db, raw_dir, pub_id = seeded
    pub_dir = raw_dir / "SBER" / pub_id
    rel = "_unpacked/balance.pdf"
    body = (
        "Sberbank Group. Consolidated financial statements (IFRS).\n"
        "Statement of Financial Position as at 31 December 2025.\n"
    ) * 30
    make_text_pdf(pub_dir / rel, body)

    with closing(db.connect()) as conn:
        DocumentsRepo(db, conn).add_documents(
            pub_id,
            [
                DocumentInput(
                    relative_path=rel,
                    file_hash="h1",
                    mime_type="application/pdf",
                )
            ],
        )

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = ClassifierService(
            publications_repo,
            documents_repo,
            raw_dir=raw_dir,
            metrics_config=_METRICS_CONFIG,
            min_text_chars_per_page=50,
            first_pages_to_inspect=1,
        )
        pub = publications_repo.get_by_id(pub_id)
        assert pub is not None
        outcomes = service.run([pub])
        docs = documents_repo.list_for_publication(pub_id)
        pub_after = publications_repo.get_by_id(pub_id)

    assert len(outcomes) == 1
    assert outcomes[0].pdf_count == 1
    assert outcomes[0].machine_readable_count == 1
    assert outcomes[0].standards["IFRS"] == 1

    assert len(docs) == 1
    doc = docs[0]
    assert doc.is_machine_readable == 1
    assert doc.reporting_standard == "IFRS"
    # The fixture has both IFRS markers and "Statement of Financial Position".
    assert doc.report_form == "balance_sheet"
    assert doc.page_count == 1

    assert pub_after is not None and pub_after.status == "classified"


def test_scan_pdf_is_marked_not_machine_readable(
    tmp_path: Path,
    make_scan_pdf: Callable[[Path], Path],
    seeded: tuple[Database, Path, str],
) -> None:
    db, raw_dir, pub_id = seeded
    pub_dir = raw_dir / "SBER" / pub_id
    rel = "_unpacked/scan.pdf"
    make_scan_pdf(pub_dir / rel)

    with closing(db.connect()) as conn:
        DocumentsRepo(db, conn).add_documents(
            pub_id,
            [
                DocumentInput(
                    relative_path=rel,
                    file_hash="h2",
                    mime_type="application/pdf",
                )
            ],
        )

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = ClassifierService(
            publications_repo,
            documents_repo,
            raw_dir=raw_dir,
            metrics_config=_METRICS_CONFIG,
            min_text_chars_per_page=50,
            first_pages_to_inspect=1,
        )
        pub = publications_repo.get_by_id(pub_id)
        assert pub is not None
        outcomes = service.run([pub])
        docs = documents_repo.list_for_publication(pub_id)

    assert outcomes[0].scan_count == 1
    assert outcomes[0].machine_readable_count == 0
    doc = docs[0]
    assert doc.is_machine_readable == 0
    assert doc.reporting_standard == "OTHER"
    assert doc.report_form == "other"


def test_non_pdf_documents_skipped(
    tmp_path: Path,
    make_text_pdf: Callable[[Path, str], Path],
    seeded: tuple[Database, Path, str],
) -> None:
    db, raw_dir, pub_id = seeded
    pub_dir = raw_dir / "SBER" / pub_id
    (pub_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    pdf_rel = "_unpacked/r.pdf"
    body = "IFRS Group consolidated financial statements 2025\n" * 30
    make_text_pdf(pub_dir / pdf_rel, body)

    with closing(db.connect()) as conn:
        DocumentsRepo(db, conn).add_documents(
            pub_id,
            [
                DocumentInput(
                    relative_path="index.html",
                    file_hash="h-html",
                    mime_type="text/html",
                ),
                DocumentInput(
                    relative_path=pdf_rel,
                    file_hash="h-pdf",
                    mime_type="application/pdf",
                ),
            ],
        )

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = ClassifierService(
            publications_repo,
            documents_repo,
            raw_dir=raw_dir,
            metrics_config=_METRICS_CONFIG,
            min_text_chars_per_page=50,
            first_pages_to_inspect=1,
        )
        pub = publications_repo.get_by_id(pub_id)
        assert pub is not None
        service.run([pub])
        docs = documents_repo.list_for_publication(pub_id)

    by_path = {d.relative_path: d for d in docs}
    # Non-PDF: classification fields stay NULL.
    html_doc = by_path["index.html"]
    assert html_doc.is_machine_readable is None
    assert html_doc.reporting_standard is None
    assert html_doc.report_form is None
    # PDF: classified.
    pdf_doc = by_path[pdf_rel]
    assert pdf_doc.is_machine_readable == 1
    assert pdf_doc.reporting_standard == "IFRS"


def test_publication_with_unreadable_pdf_continues_pipeline(
    tmp_path: Path,
    seeded: tuple[Database, Path, str],
) -> None:
    db, raw_dir, pub_id = seeded
    pub_dir = raw_dir / "SBER" / pub_id
    bad = pub_dir / "broken.pdf"
    bad.write_bytes(b"not actually a pdf")

    with closing(db.connect()) as conn:
        DocumentsRepo(db, conn).add_documents(
            pub_id,
            [
                DocumentInput(
                    relative_path="broken.pdf",
                    file_hash="hb",
                    mime_type="application/pdf",
                )
            ],
        )

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = ClassifierService(
            publications_repo,
            documents_repo,
            raw_dir=raw_dir,
            metrics_config=_METRICS_CONFIG,
            min_text_chars_per_page=50,
            first_pages_to_inspect=1,
        )
        pub = publications_repo.get_by_id(pub_id)
        assert pub is not None
        outcomes = service.run([pub])
        pub_after = publications_repo.get_by_id(pub_id)

    # The single bad PDF is unreadable but the publication still ends 'classified'
    # — we count it as a PDF, but skip its update_classification.
    assert outcomes[0].pdf_count == 1
    assert outcomes[0].machine_readable_count == 0
    assert outcomes[0].scan_count == 0
    assert pub_after is not None and pub_after.status == "classified"


def _seed_pub_with_real_pdf(
    tmp_path: Path,
    *,
    src_pdf: Path,
    rel_dest: str = "_unpacked/r.pdf",
    pub_id: str = "pub-real",
) -> tuple[Database, Path, str]:
    """Set up a workspace whose pub-1 directory contains a real fixture PDF."""
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    raw_dir = tmp_path / "raw"
    pub_dir = raw_dir / "SBER" / pub_id
    pub_dir.mkdir(parents=True)
    (pub_dir / rel_dest).parent.mkdir(parents=True, exist_ok=True)
    (pub_dir / rel_dest).write_bytes(src_pdf.read_bytes())

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
                    relative_path=rel_dest,
                    file_hash="real-h",
                    mime_type="application/pdf",
                )
            ],
        )
    return db, raw_dir, pub_id


def _classify(db: Database, raw_dir: Path, pub_id: str) -> None:
    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        service = ClassifierService(
            publications_repo,
            documents_repo,
            raw_dir=raw_dir,
            metrics_config=_METRICS_CONFIG,
            min_text_chars_per_page=50,
            first_pages_to_inspect=3,
        )
        pub = publications_repo.get_by_id(pub_id)
        assert pub is not None
        service.run([pub])


def test_classifier_writes_pages_classification_for_sber_rpbu_hybrid(
    tmp_path: Path,
) -> None:
    """Patch 18: SBER RSBU 9M 2025 → text=4, scan=13, JSON populated."""
    db, raw_dir, pub_id = _seed_pub_with_real_pdf(
        tmp_path, src_pdf=REAL_FIXTURES / "sber_rpbu_9m2025.pdf"
    )
    _classify(db, raw_dir, pub_id)
    with closing(db.connect()) as conn:
        docs = DocumentsRepo(db, conn).list_for_publication(pub_id)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.text_pages_count == 4
    assert doc.scan_pages_count == 13
    # Aggregate flag stays True — at least one text page.
    assert doc.is_machine_readable == 1
    assert doc.pages_classification is not None
    parsed = json.loads(doc.pages_classification)
    assert len(parsed) == 17
    text_pages = sorted(e["page"] for e in parsed if e["kind"] == "text")
    scan_pages = sorted(e["page"] for e in parsed if e["kind"] == "scan")
    assert text_pages == [0, 1, 2, 3]
    assert scan_pages == list(range(4, 17))


def test_classifier_writes_pages_classification_for_lkoh_rsbu_all_text(
    tmp_path: Path,
) -> None:
    """Anti-regression: pure-text RSBU document — scan_pages_count == 0."""
    db, raw_dir, pub_id = _seed_pub_with_real_pdf(
        tmp_path, src_pdf=REAL_FIXTURES / "lkoh_rsbu_q1_2026.pdf"
    )
    _classify(db, raw_dir, pub_id)
    with closing(db.connect()) as conn:
        docs = DocumentsRepo(db, conn).list_for_publication(pub_id)
    doc = docs[0]
    assert doc.text_pages_count == 24
    assert doc.scan_pages_count == 0
    assert doc.is_machine_readable == 1
    parsed = json.loads(doc.pages_classification or "[]")
    assert all(e["kind"] == "text" for e in parsed)


def test_classifier_writes_pages_classification_for_vtb_go_all_text(
    tmp_path: Path,
) -> None:
    """Anti-regression for a different banking issuer's annual report."""
    db, raw_dir, pub_id = _seed_pub_with_real_pdf(
        tmp_path, src_pdf=REAL_FIXTURES / "vtb_go_2024_first30.pdf"
    )
    _classify(db, raw_dir, pub_id)
    with closing(db.connect()) as conn:
        docs = DocumentsRepo(db, conn).list_for_publication(pub_id)
    doc = docs[0]
    assert doc.text_pages_count == 30
    assert doc.scan_pages_count == 0
    assert doc.is_machine_readable == 1
