"""Patch 33: vision-fallback retry on low coverage.

Spins up a stripped-down MetricExtractorService with a fake LLM that
returns a low-coverage payload first and a high-coverage payload on
the retry. Verifies that:

- the fallback fires only when ``vision_fallback_enabled=True``,
- it fires only when the doc has scan pages and the provider supports
  PDF input,
- the merged result wins (Patch 26 dedup picks non-null over null),
- ``vision_fallback_max_pages`` actually caps the number of pages sent.
"""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from edx.config import TickerEntry, TickersConfig
from edx.providers.llm import LLMRequest, LLMResponse
from edx.stages.metric_extractor.service import MetricExtractorService
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    MetricsRepo,
    PublicationsRepo,
    TickersRepo,
)
from tests.stages.metric_extractor.test_service import _METRICS_CONFIG


@dataclass
class _SequentialFakeLLM:
    """Returns canned responses in order; records every received request."""

    payloads: list[dict[str, Any]]
    name: str = "fake-anthropic"
    supports_pdf_input: bool = True
    calls: list[LLMRequest] = field(default_factory=list)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        idx = len(self.calls) - 1
        payload = self.payloads[min(idx, len(self.payloads) - 1)]
        return LLMResponse(
            data=payload,
            raw_text=json.dumps(payload, ensure_ascii=False),
            provider=self.name,
            model="m",
            input_tokens=100,
            output_tokens=20,
        )


_TICKERS = TickersConfig(
    tickers=[
        TickerEntry(
            ticker="CHMF",
            e_disclosure_id="3",
            name="Severstal",
            profile="non_bank",
        )
    ]
)


def _empty_payload() -> dict[str, Any]:
    return {
        "extractions": [
            {
                "reporting_date": "2025-12-31",
                "period_type": "FY",
                "reporting_standard": "RSBU",
                "currency": "RUB",
                "unit": "ones",
                "metrics": {
                    "revenue": {"value": None, "source_quote": None},
                    "ebitda": {"value": None, "source_quote": None},
                    "net_income": {"value": None, "source_quote": None},
                    "total_assets": {"value": None, "source_quote": None},
                    "total_debt": {"value": None, "source_quote": None},
                },
            }
        ]
    }


def _filled_payload() -> dict[str, Any]:
    return {
        "extractions": [
            {
                "reporting_date": "2025-12-31",
                "period_type": "FY",
                "reporting_standard": "RSBU",
                "currency": "RUB",
                "unit": "ones",
                "metrics": {
                    "revenue": {
                        "value": 620_099_738_000.0,
                        "source_quote": "Выручка 620 099 738",
                    },
                    "net_income": {
                        "value": 112_756_992_000.0,
                        "source_quote": "Чистая прибыль 112 756 992",
                    },
                    "total_assets": {
                        "value": 846_546_320_000.0,
                        "source_quote": "БАЛАНС 846 546 320",
                    },
                    "total_debt": {
                        "value": 102_337_218_000.0,
                        "source_quote": "Заёмные средства 102 337 218",
                    },
                    "ebitda": {"value": None, "source_quote": None},
                },
            }
        ]
    }


def _build_workspace(
    tmp_path: Path,
    *,
    pages_classification: list[dict[str, object]],
    page_count: int,
    text_pages_count: int,
    scan_pages_count: int,
) -> tuple[Database, Path, Path, str]:
    """Provision SQLite + dirs + a single CHMF RSBU publication. Returns the
    publication_id; the document at relative_path 'doc.pdf' contains a fake
    multi-page PDF written to disk so vision-fallback's read_bytes() works.
    """
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    raw.mkdir()
    proc.mkdir()

    pub_id = "CHMF-3-FY2025"
    with closing(db.connect()) as conn:
        TickersRepo(db, conn).upsert_from_config(
            [TickerEntry(ticker="CHMF", e_disclosure_id="3", name="Severstal")]
        )
        pubs = PublicationsRepo(db, conn)
        docs = DocumentsRepo(db, conn)
        pubs.upsert_discovered(
            publication_id=pub_id,
            ticker="CHMF",
            publication_type="report",
            publication_date="2026-02-27",
            source_url="https://example.test/r.zip",
        )
        for status in ("downloaded", "unpacked", "classified", "extracted"):
            pubs.mark_status(pub_id, status)  # type: ignore[arg-type]
        docs.add_documents(
            pub_id,
            [DocumentInput(relative_path="doc.pdf", file_hash="h1", mime_type="application/pdf")],
        )
        doc_id = docs.list_for_publication(pub_id)[0].document_id
        docs.update_classification(
            doc_id,
            reporting_standard="RSBU",
            report_form="balance_sheet",
            is_machine_readable=True,
            page_count=page_count,
            pages_classification=json.dumps(pages_classification),
            text_pages_count=text_pages_count,
            scan_pages_count=scan_pages_count,
        )

    # A fake multi-page PDF on disk — pymupdf accepts a minimal handcrafted
    # blob, but creating one programmatically is shorter and matches the
    # page_count we promised the documents repo.
    import pymupdf

    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    for _ in range(page_count):
        doc.new_page()  # type: ignore[no-untyped-call]
    target = raw / "CHMF" / pub_id / "doc.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]

    return db, raw, proc, pub_id


def _make_service(
    db: Database,
    raw: Path,
    proc: Path,
    llm: _SequentialFakeLLM,
    *,
    vision_fallback_enabled: bool,
    vision_fallback_max_pages: int = 12,
) -> tuple[MetricExtractorService, object]:
    conn = db.connect()
    service = MetricExtractorService(
        llm,  # type: ignore[arg-type]
        PublicationsRepo(db, conn),
        DocumentsRepo(db, conn),
        MetricsRepo(db, conn),
        metrics_config=_METRICS_CONFIG,
        tickers_config=_TICKERS,
        raw_dir=raw,
        processed_dir=proc,
        max_tokens=2048,
        temperature=0.0,
        completeness_threshold=0.5,
        # Patch 29: keep RSBU on text-path so the first call goes through
        # _assemble_user_text and we can drive coverage with the fake LLM.
        scan_ratio_threshold=0.10,
        pdf_input_standards=("IFRS",),
        vision_fallback_enabled=vision_fallback_enabled,
        vision_fallback_threshold=0.5,
        vision_fallback_max_pages=vision_fallback_max_pages,
    )
    return service, conn


def _scan_classification(text_pages: int, scan_pages: int) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for i in range(text_pages):
        out.append({"page": i, "chars": 200, "kind": "text"})
    for j in range(scan_pages):
        out.append({"page": text_pages + j, "chars": 5, "kind": "scan"})
    return out


@pytest.mark.asyncio
async def test_disabled_by_default(tmp_path: Path) -> None:
    """vision_fallback_enabled=False → no retry even at coverage=0."""
    db, raw, proc, pub_id = _build_workspace(
        tmp_path,
        pages_classification=_scan_classification(2, 4),
        page_count=6,
        text_pages_count=2,
        scan_pages_count=4,
    )
    llm = _SequentialFakeLLM(payloads=[_empty_payload()])
    service, conn = _make_service(db, raw, proc, llm, vision_fallback_enabled=False)
    try:
        from edx.storage import PublicationsRepo as _PR

        pubs = _PR(db, conn).list_by_status("extracted")
        await service.run(pubs)
    finally:
        conn.close()
    assert len(llm.calls) == 1  # exactly one LLM call


@pytest.mark.asyncio
async def test_triggered_on_low_coverage(tmp_path: Path) -> None:
    """First pass → 0/4 (skipping ebitda which is RSBU-excluded); retry
    fills 4/4. Final outcome reflects the merged result.
    """
    db, raw, proc, pub_id = _build_workspace(
        tmp_path,
        pages_classification=_scan_classification(2, 4),
        page_count=6,
        text_pages_count=2,
        scan_pages_count=4,
    )
    llm = _SequentialFakeLLM(
        payloads=[_empty_payload(), _filled_payload()]
    )
    service, conn = _make_service(db, raw, proc, llm, vision_fallback_enabled=True)
    try:
        from edx.storage import PublicationsRepo as _PR

        pubs = _PR(db, conn).list_by_status("extracted")
        outcomes = await service.run(pubs)
    finally:
        conn.close()
    assert len(llm.calls) == 2  # primary + vision fallback
    # First call was text-path (no PDF), second was vision (has PDF + indices).
    assert llm.calls[0].pdf_bytes is None
    assert llm.calls[1].pdf_bytes is not None
    assert llm.calls[1].pdf_page_indices is not None
    # Vision retry sent only scan pages (indices 2..5 in our fixture).
    assert all(idx >= 2 for idx in llm.calls[1].pdf_page_indices)
    # Coverage lifted by the fallback.
    assert outcomes[0].coverage_ratio >= 0.5


@pytest.mark.asyncio
async def test_skipped_when_no_scan_pages(tmp_path: Path) -> None:
    """page-count > 0 but scan_pages_count = 0 → nothing to OCR with vision."""
    db, raw, proc, pub_id = _build_workspace(
        tmp_path,
        pages_classification=_scan_classification(6, 0),
        page_count=6,
        text_pages_count=6,
        scan_pages_count=0,
    )
    llm = _SequentialFakeLLM(payloads=[_empty_payload()])
    service, conn = _make_service(db, raw, proc, llm, vision_fallback_enabled=True)
    try:
        from edx.storage import PublicationsRepo as _PR

        pubs = _PR(db, conn).list_by_status("extracted")
        await service.run(pubs)
    finally:
        conn.close()
    assert len(llm.calls) == 1  # only the primary, no vision retry


@pytest.mark.asyncio
async def test_skipped_when_provider_does_not_support_pdf(
    tmp_path: Path,
) -> None:
    """OpenRouter (supports_pdf_input=False) cannot run vision-fallback."""
    db, raw, proc, pub_id = _build_workspace(
        tmp_path,
        pages_classification=_scan_classification(2, 4),
        page_count=6,
        text_pages_count=2,
        scan_pages_count=4,
    )
    llm = _SequentialFakeLLM(
        payloads=[_empty_payload()], supports_pdf_input=False
    )
    service, conn = _make_service(db, raw, proc, llm, vision_fallback_enabled=True)
    try:
        from edx.storage import PublicationsRepo as _PR

        pubs = _PR(db, conn).list_by_status("extracted")
        await service.run(pubs)
    finally:
        conn.close()
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_max_pages_cap_truncates(tmp_path: Path) -> None:
    """30 scan pages, max_pages=5 → vision retry sends 5."""
    db, raw, proc, pub_id = _build_workspace(
        tmp_path,
        pages_classification=_scan_classification(2, 30),
        page_count=32,
        text_pages_count=2,
        scan_pages_count=30,
    )
    llm = _SequentialFakeLLM(
        payloads=[_empty_payload(), _filled_payload()]
    )
    service, conn = _make_service(
        db, raw, proc, llm,
        vision_fallback_enabled=True,
        vision_fallback_max_pages=5,
    )
    try:
        from edx.storage import PublicationsRepo as _PR

        pubs = _PR(db, conn).list_by_status("extracted")
        await service.run(pubs)
    finally:
        conn.close()
    assert llm.calls[1].pdf_page_indices is not None
    assert len(llm.calls[1].pdf_page_indices) == 5
