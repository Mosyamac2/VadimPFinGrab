"""Patch 34: per-ticker full-vision opt-in.

When TickerEntry.use_vision_extraction=True, the Metric Extractor
renders every page of the primary doc to PNG and ships them as image
content blocks for RSBU and ISSUER. IFRS for the same ticker stays
on the standard PDF path. Globally killable via app.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from edx.config import TickerEntry, TickersConfig
from edx.providers.llm import LLMRequest, LLMResponse
from edx.stages.metric_extractor.service import MetricExtractorService
from edx.storage import DocumentRow, PublicationRow
from tests.stages.metric_extractor.test_service import _METRICS_CONFIG


@dataclass
class _FakeLLM:
    supports_pdf_input: bool = True
    name: str = "fake-anthropic"

    async def complete(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


def _tickers(use_vision: bool) -> TickersConfig:
    return TickersConfig(
        tickers=[
            TickerEntry(
                ticker="CHMF",
                e_disclosure_id="3",
                name="Severstal",
                profile="non_bank",
                use_vision_extraction=use_vision,
            )
        ]
    )


def _make_pub(pub_id: str = "CHMF-3-fy") -> PublicationRow:
    return PublicationRow(
        publication_id=pub_id,
        ticker="CHMF",
        publication_type="report",
        publication_date="2026-02-27",
        source_url="https://example.test/r.zip",
        file_hash=None,
        status="extracted",
        last_error=None,
        discovered_at="2026-02-27T00:00:00+00:00",
        updated_at="2026-02-27T00:00:00+00:00",
    )


def _make_doc(
    *, page_count: int = 8, scan_pages: int = 4, standard: str = "RSBU"
) -> DocumentRow:
    return DocumentRow(
        document_id=1,
        publication_id="CHMF-3-fy",
        relative_path="doc.pdf",
        mime_type="application/pdf",
        reporting_standard=standard,  # type: ignore[arg-type]
        report_form="balance_sheet",
        is_machine_readable=1,
        page_count=page_count,
        file_hash="h1",
        text_extract_path=None,
        pages_classification=None,
        text_pages_count=page_count - scan_pages,
        scan_pages_count=scan_pages,
    )


def _put_real_pdf(raw_dir: Path, pub: PublicationRow, num_pages: int) -> Path:
    """Write a small but valid multi-page PDF that pymupdf can render."""
    target = raw_dir / pub.ticker / pub.publication_id / "doc.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    for _ in range(num_pages):
        doc.new_page()  # type: ignore[no-untyped-call]
    doc.save(str(target))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]
    return target


def _make_service(
    *,
    raw_dir: Path,
    processed_dir: Path,
    use_vision: bool,
    global_disabled: bool = False,
    max_pages: int = 25,
) -> MetricExtractorService:
    return MetricExtractorService(
        _FakeLLM(),  # type: ignore[arg-type]
        publications_repo=None,  # type: ignore[arg-type]
        documents_repo=None,  # type: ignore[arg-type]
        metrics_repo=None,  # type: ignore[arg-type]
        metrics_config=_METRICS_CONFIG,
        tickers_config=_tickers(use_vision),
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        max_tokens=2048,
        temperature=0.0,
        completeness_threshold=0.5,
        scan_ratio_threshold=0.10,
        pdf_input_standards=("IFRS",),
        vision_only_global_disabled=global_disabled,
        vision_only_max_pages_per_request=max_pages,
    )


def test_flag_disabled_uses_normal_path(tmp_path: Path) -> None:
    """Ticker without the flag → request without pdf_page_images."""
    pub = _make_pub()
    doc = _make_doc()
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_real_pdf(raw, pub, num_pages=8)
    service = _make_service(raw_dir=raw, processed_dir=proc, use_vision=False)
    profile = _METRICS_CONFIG.for_profile("non_bank")
    req = service._build_request(pub, [doc], profile, "RSBU")
    assert req.pdf_page_images is None


def test_flag_enabled_for_rsbu_uses_vision_path(tmp_path: Path) -> None:
    """Flagged ticker on RSBU → pdf_page_images filled, pdf_bytes None."""
    pub = _make_pub()
    doc = _make_doc(page_count=4)
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_real_pdf(raw, pub, num_pages=4)
    service = _make_service(raw_dir=raw, processed_dir=proc, use_vision=True)
    profile = _METRICS_CONFIG.for_profile("non_bank")
    req = service._build_request(pub, [doc], profile, "RSBU")
    assert req.pdf_bytes is None
    assert req.pdf_page_images is not None
    assert len(req.pdf_page_images) == 4
    assert all(img.startswith(b"\x89PNG") for img in req.pdf_page_images)


def test_flag_enabled_for_ifrs_skips_vision_path(tmp_path: Path) -> None:
    """Flagged ticker on IFRS → standard PDF path (vision-only is RSBU/ISSUER only)."""
    pub = _make_pub()
    doc = _make_doc(standard="IFRS", scan_pages=0)
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_real_pdf(raw, pub, num_pages=4)
    service = _make_service(raw_dir=raw, processed_dir=proc, use_vision=True)
    profile = _METRICS_CONFIG.for_profile("non_bank")
    req = service._build_request(pub, [doc], profile, "IFRS")
    # IFRS goes to native PDF path under default pdf_input_standards.
    assert req.pdf_page_images is None
    assert req.pdf_bytes is not None


def test_global_kill_switch_overrides_ticker_flag(tmp_path: Path) -> None:
    """vision_only_global_disabled=true → ticker flag ignored."""
    pub = _make_pub()
    doc = _make_doc()
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_real_pdf(raw, pub, num_pages=4)
    service = _make_service(
        raw_dir=raw,
        processed_dir=proc,
        use_vision=True,
        global_disabled=True,
    )
    profile = _METRICS_CONFIG.for_profile("non_bank")
    req = service._build_request(pub, [doc], profile, "RSBU")
    assert req.pdf_page_images is None


def test_max_pages_cap_truncates(tmp_path: Path) -> None:
    """40-page PDF, max_pages=10 → 10 PNG images in request."""
    pub = _make_pub()
    doc = _make_doc(page_count=40, scan_pages=20)
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    proc.mkdir()
    _put_real_pdf(raw, pub, num_pages=40)
    service = _make_service(
        raw_dir=raw, processed_dir=proc, use_vision=True, max_pages=10
    )
    profile = _METRICS_CONFIG.for_profile("non_bank")
    req = service._build_request(pub, [doc], profile, "RSBU")
    assert req.pdf_page_images is not None
    assert len(req.pdf_page_images) == 10
