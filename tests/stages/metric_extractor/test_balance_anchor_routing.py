"""Patch 30: balance-anchor trim wired into MetricExtractorService._assemble_user_text.

Verifies that when a publication's source_standard is RSBU and the
text-extract has an audit preamble, the assembler trims to the first
balance-form anchor; for IFRS the same input is sent verbatim; and
when no anchor matches the assembler falls back to the full text
(emitting a warning, not crashing).
"""

from __future__ import annotations

import json
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
    supports_pdf_input: bool = True
    name: str = "fake-anthropic"

    async def complete(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


_AUDIT_PREAMBLE = (
    "Аудиторское заключение независимых аудиторов\n"
    "ПАО «Тестовая Компания» за 2025 год.\n"
    "Мнение аудитора: достоверно.\n"
    "Ключевые вопросы аудита: оценка обесценения.\n"
) * 50  # ~5k chars of audit noise on multiple lines
_FORM_BODY = (
    "БУХГАЛТЕРСКИЙ БАЛАНС\n"
    "АКТИВ\n"
    "Основные средства 1150 397 216 398\n"
    "Запасы 1210 73 327 449\n"
    "БАЛАНС 1700 846 546 320\n"
)


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


def _make_doc_with_text(
    *,
    processed_dir: Path,
    pub_id: str,
    document_id: int,
    text: str,
    standard: str,
) -> DocumentRow:
    rel_path = Path("SBER") / pub_id / f"{document_id}.json"
    full = processed_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(
        json.dumps(
            {"pages": [{"page_number": 1, "text": text}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return DocumentRow(
        document_id=document_id,
        publication_id=pub_id,
        relative_path=f"_unpacked/doc{document_id}.pdf",
        mime_type="application/pdf",
        reporting_standard=standard,  # type: ignore[arg-type]
        report_form="balance_sheet",
        is_machine_readable=0,  # scan-only forces text-path
        page_count=1,
        file_hash=f"h{document_id}",
        text_extract_path=str(rel_path),
        pages_classification=None,
        text_pages_count=0,
        scan_pages_count=1,
    )


def _make_service(
    *, raw_dir: Path, processed_dir: Path
) -> MetricExtractorService:
    llm = _FakeLLM()
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
    )


def test_rsbu_text_path_invokes_balance_trim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    proc = tmp_path / "proc"
    proc.mkdir()
    raw = tmp_path / "raw"
    pub = _make_pub("CHMF-3-1913112")
    doc = _make_doc_with_text(
        processed_dir=proc,
        pub_id=pub.publication_id,
        document_id=77,
        text=_AUDIT_PREAMBLE + _FORM_BODY,
        standard="RSBU",
    )
    service = _make_service(raw_dir=raw, processed_dir=proc)

    user_text = service._assemble_user_text(
        pub, [doc], "RSBU", "non_bank"
    )
    # structlog routes INFO to stdout and WARNING through std logging —
    # combine both so the assertion does not depend on which channel
    # the level happens to flow through.
    log_out = capsys.readouterr().out + caplog.text

    # Trim event surfaces in structlog JSON output (stdout).
    assert "metric_extract_balance_anchor_trimmed" in log_out
    assert "metric_extract_balance_anchor_missing" not in log_out
    # Audit preamble cut, form body retained.
    assert "Аудиторское заключение" not in user_text
    assert "БУХГАЛТЕРСКИЙ БАЛАНС" in user_text
    assert "БАЛАНС 1700 846 546 320" in user_text
    # Lead header injected.
    assert "Перед тобой формы РСБУ-отчётности" in user_text


def test_ifrs_text_path_does_not_invoke_balance_trim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    proc = tmp_path / "proc"
    proc.mkdir()
    raw = tmp_path / "raw"
    pub = _make_pub()
    doc = _make_doc_with_text(
        processed_dir=proc,
        pub_id=pub.publication_id,
        document_id=1,
        text=_AUDIT_PREAMBLE + _FORM_BODY,
        standard="IFRS",
    )
    service = _make_service(raw_dir=raw, processed_dir=proc)

    user_text = service._assemble_user_text(
        pub, [doc], "IFRS", "non_bank"
    )
    # structlog routes INFO to stdout and WARNING through std logging —
    # combine both so the assertion does not depend on which channel
    # the level happens to flow through.
    log_out = capsys.readouterr().out + caplog.text

    # No balance-anchor events for IFRS.
    assert "metric_extract_balance_anchor" not in log_out
    # Audit preamble preserved (it's not trimmed for IFRS).
    assert "Аудиторское заключение" in user_text


def test_rsbu_no_anchor_falls_back_to_full_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the doc has no balance/P&L anchor, the assembler keeps
    the full text and emits a warning instead of crashing.
    """
    proc = tmp_path / "proc"
    proc.mkdir()
    raw = tmp_path / "raw"
    pub = _make_pub()
    doc = _make_doc_with_text(
        processed_dir=proc,
        pub_id=pub.publication_id,
        document_id=1,
        text="Аудиторское заключение. Конец. Подпись.",
        standard="RSBU",
    )
    service = _make_service(raw_dir=raw, processed_dir=proc)

    user_text = service._assemble_user_text(
        pub, [doc], "RSBU", "non_bank"
    )
    # structlog routes INFO to stdout and WARNING through std logging —
    # combine both so the assertion does not depend on which channel
    # the level happens to flow through.
    log_out = capsys.readouterr().out + caplog.text

    # Warning event present in JSON output.
    assert "metric_extract_balance_anchor_missing" in log_out
    assert "Аудиторское заключение. Конец. Подпись." in user_text
