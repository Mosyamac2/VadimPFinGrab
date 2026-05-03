"""Patch 36: RTF support — extraction + classifier integration."""

from __future__ import annotations

from pathlib import Path

from edx.stages.text_extractor.native import extract_text_from_rtf

_MIN_RTF = (
    r"{\rtf1\ansi\ansicpg1251"
    r"\par Отчёт эмитента ПАО «Тестовая Компания» за 6 месяцев 2025 года."
    r"\par Чистая прибыль 1 234 567 тыс. руб."
    r"}"
)


def _write_rtf(tmp_path: Path, name: str, body: str) -> Path:
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return target


def test_extract_text_from_rtf_returns_single_page(tmp_path: Path) -> None:
    pages = extract_text_from_rtf(_write_rtf(tmp_path, "x.rtf", _MIN_RTF))
    assert len(pages) == 1
    assert pages[0].page_number == 1


def test_extract_text_from_rtf_preserves_cyrillic(tmp_path: Path) -> None:
    pages = extract_text_from_rtf(_write_rtf(tmp_path, "x.rtf", _MIN_RTF))
    text = pages[0].text
    assert "Чистая прибыль" in text
    assert "1 234 567" in text
    assert "Тестовая Компания" in text


def test_extract_text_from_rtf_strips_control_words(tmp_path: Path) -> None:
    body = (
        r"{\rtf1\ansi"
        r"\b\i\par Жирно и курсивно \b0\i0"
        r"\par Обычный текст."
        r"}"
    )
    pages = extract_text_from_rtf(_write_rtf(tmp_path, "x.rtf", body))
    text = pages[0].text
    assert r"\b" not in text
    assert r"\i" not in text
    assert r"\par" not in text
    assert "Жирно и курсивно" in text
    assert "Обычный текст" in text


def test_extract_text_from_rtf_handles_empty_file(tmp_path: Path) -> None:
    pages = extract_text_from_rtf(_write_rtf(tmp_path, "empty.rtf", ""))
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].text == ""


def test_extract_text_from_rtf_handles_broken_rtf(tmp_path: Path) -> None:
    """Garbled RTF input — striprtf swallows; we never crash."""
    pages = extract_text_from_rtf(
        _write_rtf(tmp_path, "broken.rtf", "не RTF, просто текст без braces")
    )
    assert len(pages) == 1
    # Plain (non-RTF) input may yield empty or echo of the source —
    # either way, no exception.
    assert isinstance(pages[0].text, str)


def test_classifier_handles_rtf_with_type_code_mapping(tmp_path: Path) -> None:
    """ClassifierService classifies an RTF document via type_code mapping
    instead of running per-page text/scan analysis (which doesn't apply
    to non-PDF formats).
    """
    from contextlib import closing

    from edx.config import MetricsConfig, MetricSpec, MetricsProfile, TickerEntry
    from edx.stages.classifier.service import ClassifierService
    from edx.storage import (
        Database,
        DocumentInput,
        DocumentsRepo,
        PublicationsRepo,
        TickersRepo,
    )

    # Workspace setup.
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    metrics_cfg = MetricsConfig(
        profiles={
            "non_bank": MetricsProfile(
                metrics={"x": MetricSpec(synonyms=["x"])},
                reporting_priority=["IFRS", "RSBU", "ISSUER"],
            ),
            "bank": MetricsProfile(
                metrics={"y": MetricSpec(synonyms=["y"])},
                reporting_priority=["IFRS", "RSBU", "ISSUER"],
            ),
        }
    )

    pub_id = "PHOR-5-RTF"
    with closing(db.connect()) as conn:
        TickersRepo(db, conn).upsert_from_config(
            [TickerEntry(ticker="PHOR", e_disclosure_id="573", name="ФосАгро")]
        )
        pubs = PublicationsRepo(db, conn)
        docs = DocumentsRepo(db, conn)
        pubs.upsert_discovered(
            publication_id=pub_id,
            ticker="PHOR",
            publication_type="report",
            publication_date="2026-04-01",
            source_url="https://example.test/r.rtf",
            report_type_code=5,  # → ISSUER
            report_type_label="Отчет эмитента",
        )
        for st in ("downloaded", "unpacked"):
            pubs.mark_status(pub_id, st)  # type: ignore[arg-type]
        rel = "_unpacked/report.rtf"
        # Put a real RTF on disk (classifier doesn't read it but the
        # downstream Text Extractor will — we just exercise the classifier
        # branch here).
        full = raw_dir / "PHOR" / pub_id / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(_MIN_RTF, encoding="utf-8")
        docs.add_documents(
            pub_id,
            [DocumentInput(relative_path=rel, file_hash="h1", mime_type="application/rtf")],
        )
        pub_row = pubs.get_by_id(pub_id)
        assert pub_row is not None

    conn = db.connect()
    try:
        service = ClassifierService(
            PublicationsRepo(db, conn),
            DocumentsRepo(db, conn),
            raw_dir=raw_dir,
            metrics_config=metrics_cfg,
        )
        outcomes = service.run([pub_row])
    finally:
        conn.close()

    conn = db.connect()
    try:
        docs_after = DocumentsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()
    assert len(docs_after) == 1
    doc = docs_after[0]
    assert doc.reporting_standard == "ISSUER"
    assert doc.is_machine_readable == 1
    assert doc.page_count == 1
    assert doc.text_pages_count == 1
    assert doc.scan_pages_count == 0
    # Outcome counter bumped for the RTF too.
    assert outcomes[0].pdf_count == 1
    assert outcomes[0].machine_readable_count == 1
    assert outcomes[0].standards["ISSUER"] == 1
