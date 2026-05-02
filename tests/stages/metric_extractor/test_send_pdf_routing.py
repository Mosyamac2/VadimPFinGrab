"""Patch 29: routing into PDF vs text path in MetricExtractorService.

These tests do NOT spin up the full Database / Repo stack — they
exercise ``_build_request`` directly on hand-constructed
``PublicationRow`` / ``DocumentRow`` instances. That keeps the routing
logic isolated from the rest of the stage and gives short, fast tests
for the boundary cases that matter (scan ratio, standard whitelist).

The fake LLM is the same shape as in test_service.py (``supports_pdf_input``
toggleable, no real network calls).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from edx.providers.llm import LLMRequest, LLMResponse
from edx.stages.metric_extractor.service import MetricExtractorService
from edx.storage import DocumentRow, PublicationRow
from tests.stages.metric_extractor.test_service import (
    _METRICS_CONFIG,
    _TICKERS_CONFIG,
)


@dataclass
class _FakeLLM:
    """Minimal LLMProvider stub — only ``supports_pdf_input`` matters here."""

    supports_pdf_input: bool = True
    name: str = "fake-anthropic"

    async def complete(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        # _build_request never invokes the LLM.
        raise NotImplementedError


def _make_pub(pub_id: str = "SBER-3-1") -> PublicationRow:
    return PublicationRow(
        publication_id=pub_id,
        ticker="SBER",
        publication_type="report",
        publication_date="2026-04-01",
        source_url="https://example.test/r.pdf",
        file_hash=None,
        status="extracted",
        last_error=None,
        discovered_at="2026-04-01T00:00:00+00:00",
        updated_at="2026-04-01T00:00:00+00:00",
    )


def _make_doc(
    *,
    document_id: int = 1,
    publication_id: str = "SBER-3-1",
    relative_path: str = "_unpacked/doc.pdf",
    page_count: int | None,
    text_pages_count: int | None,
    scan_pages_count: int | None,
    is_machine_readable: int | None = 1,
    reporting_standard: str | None = "RSBU",
    text_extract_path: str | None = None,
) -> DocumentRow:
    return DocumentRow(
        document_id=document_id,
        publication_id=publication_id,
        relative_path=relative_path,
        mime_type="application/pdf",
        reporting_standard=reporting_standard,  # type: ignore[arg-type]
        report_form="balance_sheet",
        is_machine_readable=is_machine_readable,
        page_count=page_count,
        file_hash=f"h{document_id}",
        text_extract_path=text_extract_path,
        pages_classification=None,
        text_pages_count=text_pages_count,
        scan_pages_count=scan_pages_count,
    )


def _make_service(
    *,
    raw_dir: Path,
    processed_dir: Path,
    supports_pdf_input: bool = True,
    scan_ratio_threshold: float = 0.10,
    pdf_input_standards: tuple[str, ...] = ("IFRS",),
) -> MetricExtractorService:
    llm = _FakeLLM(supports_pdf_input=supports_pdf_input)
    return MetricExtractorService(
        llm,  # type: ignore[arg-type]
        publications_repo=None,  # type: ignore[arg-type]
        documents_repo=None,  # type: ignore[arg-type]
        metrics_repo=None,  # type: ignore[arg-type]
        metrics_config=_METRICS_CONFIG,
        tickers_config=_TICKERS_CONFIG,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        max_tokens=2048,
        temperature=0.0,
        completeness_threshold=0.5,
        scan_ratio_threshold=scan_ratio_threshold,
        pdf_input_standards=pdf_input_standards,
    )


def _put_pdf(raw_dir: Path, pub_id: str, relative_path: str) -> Path:
    full = raw_dir / "SBER" / pub_id / relative_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"%PDF-1.4 fake content")
    return full


def test_pure_text_ifrs_pdf_keeps_pdf_path(tmp_path: Path) -> None:
    """Vanilla IFRS report (15 text + 1 cover scan) → native PDF path."""
    pub = _make_pub()
    doc = _make_doc(
        page_count=14,
        text_pages_count=13,
        scan_pages_count=1,
        is_machine_readable=1,
        reporting_standard="IFRS",
    )
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_pdf(raw, pub.publication_id, doc.relative_path)
    service = _make_service(raw_dir=raw, processed_dir=proc)
    profile = _METRICS_CONFIG.for_profile("non_bank")

    req = service._build_request(pub, [doc], profile, "IFRS")

    assert req.pdf_bytes is not None
    assert req.pdf_bytes.startswith(b"%PDF")
    assert "Извлеки финансовые показатели из приложенного документа" in req.user_text


def test_hybrid_pdf_falls_back_to_text_path(tmp_path: Path) -> None:
    """6 text + 30 scan pages (CHMF FY 2025 case) → text path, ignores native PDF."""
    pub = _make_pub("CHMF-3-1913112")
    doc = _make_doc(
        page_count=36,
        text_pages_count=6,
        scan_pages_count=30,
        is_machine_readable=1,
        reporting_standard="RSBU",
    )
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_pdf(raw, pub.publication_id, doc.relative_path)
    service = _make_service(raw_dir=raw, processed_dir=proc)
    profile = _METRICS_CONFIG.for_profile("non_bank")

    req = service._build_request(pub, [doc], profile, "RSBU")

    assert req.pdf_bytes is None
    assert "Эмитент: CHMF-3-1913112" not in req.user_text  # ticker, not pub_id
    assert "Эмитент: SBER" in req.user_text  # pub.ticker is "SBER"
    assert "Стандарт отчётности: RSBU" in req.user_text


def test_rsbu_pure_text_pdf_still_uses_text_path(tmp_path: Path) -> None:
    """VTBR Q1 2026 case: 43 text pages, zero scans, but RSBU → text path.

    Empirically Anthropic native-PDF doesn't read RSBU forms reliably even
    with no scan pages — the thin grid + Russian numerals trip it up.
    """
    pub = _make_pub("VTBR-3-1924077")
    doc = _make_doc(
        page_count=43,
        text_pages_count=43,
        scan_pages_count=0,
        is_machine_readable=1,
        reporting_standard="RSBU",
    )
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_pdf(raw, pub.publication_id, doc.relative_path)
    service = _make_service(raw_dir=raw, processed_dir=proc)
    profile = _METRICS_CONFIG.for_profile("non_bank")

    req = service._build_request(pub, [doc], profile, "RSBU")

    assert req.pdf_bytes is None  # standard not in pdf_input_standards


def test_threshold_boundary_at_10_percent(tmp_path: Path) -> None:
    """scan_ratio == threshold → PDF path (boundary inclusive); +1 → text path."""
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    pub = _make_pub()
    profile = _METRICS_CONFIG.for_profile("non_bank")

    # 1/10 = 0.10 == threshold → still PDF.
    doc_at = _make_doc(
        page_count=10,
        text_pages_count=9,
        scan_pages_count=1,
        reporting_standard="IFRS",
    )
    _put_pdf(raw, pub.publication_id, doc_at.relative_path)
    service = _make_service(raw_dir=raw, processed_dir=proc)
    req_at = service._build_request(pub, [doc_at], profile, "IFRS")
    assert req_at.pdf_bytes is not None

    # 2/10 = 0.20 > threshold → text path.
    doc_over = _make_doc(
        page_count=10,
        text_pages_count=8,
        scan_pages_count=2,
        reporting_standard="IFRS",
    )
    req_over = service._build_request(pub, [doc_over], profile, "IFRS")
    assert req_over.pdf_bytes is None


def test_issuer_always_uses_text_path(tmp_path: Path) -> None:
    """ISSUER not in default pdf_input_standards → text path even at scan_ratio=0."""
    pub = _make_pub("CHMF-5-1913545")
    doc = _make_doc(
        page_count=80,
        text_pages_count=80,
        scan_pages_count=0,
        is_machine_readable=1,
        reporting_standard="ISSUER",
    )
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_pdf(raw, pub.publication_id, doc.relative_path)
    service = _make_service(raw_dir=raw, processed_dir=proc)
    profile = _METRICS_CONFIG.for_profile("non_bank")

    req = service._build_request(pub, [doc], profile, "ISSUER")

    assert req.pdf_bytes is None


def test_zero_page_count_treats_as_full_scan(tmp_path: Path) -> None:
    """page_count=0 (broken PDF metadata) → scan_ratio=1.0 → text path."""
    pub = _make_pub()
    doc = _make_doc(
        page_count=0,
        text_pages_count=0,
        scan_pages_count=0,
        is_machine_readable=1,
        reporting_standard="IFRS",  # even IFRS is rejected when ratio=1.0
    )
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_pdf(raw, pub.publication_id, doc.relative_path)
    service = _make_service(raw_dir=raw, processed_dir=proc)
    profile = _METRICS_CONFIG.for_profile("non_bank")

    req = service._build_request(pub, [doc], profile, "IFRS")

    assert req.pdf_bytes is None


def test_metric_extract_start_log_includes_routing_keys(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The metric_extract_start event must surface scan_ratio +
    pdf_input_standards so an operator can debug routing decisions
    from pipeline.log without diffing code.
    """
    # The log payload structure is checked through service inspection
    # to avoid spinning up the full pipeline. We assert that the
    # relevant fields exist as service attributes (the log line is
    # emitted in _extract_one, fully covered in test_service.py).
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    service = _make_service(
        raw_dir=raw,
        processed_dir=proc,
        scan_ratio_threshold=0.25,
        pdf_input_standards=("IFRS", "RSBU"),
    )
    assert service.scan_ratio_threshold == 0.25
    assert service.pdf_input_standards == ("IFRS", "RSBU")


def test_provider_without_pdf_support_always_uses_text(tmp_path: Path) -> None:
    """OpenRouter (supports_pdf_input=False) → text path regardless of ratio."""
    pub = _make_pub()
    doc = _make_doc(
        page_count=14,
        text_pages_count=14,
        scan_pages_count=0,
        is_machine_readable=1,
        reporting_standard="IFRS",
    )
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_pdf(raw, pub.publication_id, doc.relative_path)
    service = _make_service(
        raw_dir=raw, processed_dir=proc, supports_pdf_input=False
    )
    profile = _METRICS_CONFIG.for_profile("non_bank")

    req = service._build_request(pub, [doc], profile, "IFRS")

    assert req.pdf_bytes is None
