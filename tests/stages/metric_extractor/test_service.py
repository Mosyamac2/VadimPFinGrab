"""MetricExtractorService — priority, normalization, mocked LLM."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from edx.config import (
    MetricsConfig,
    MetricSpec,
    MetricsProfile,
    TickerEntry,
    TickersConfig,
)
from edx.providers.llm import LLMRequest, LLMResponse
from edx.stages.metric_extractor.service import (
    MetricExtractorService,
    normalize_value,
)
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    MetricsRepo,
    PublicationsRepo,
    TickersRepo,
)

_METRICS_CONFIG = MetricsConfig(
    profiles={
        "non_bank": MetricsProfile(
            metrics={
                "revenue": MetricSpec(synonyms=["Выручка"]),
                "ebitda": MetricSpec(
                    synonyms=["EBITDA"],
                    only_in_sources=["IFRS", "ISSUER"],
                ),
                "net_income": MetricSpec(synonyms=["Чистая прибыль"]),
                "total_assets": MetricSpec(synonyms=["Итого активы"]),
                "total_debt": MetricSpec(
                    synonyms=["Заемные средства"],
                    aggregation_hint="sum 1410+1510",
                ),
            },
            reporting_priority=["IFRS", "RSBU", "ISSUER"],
        ),
        "bank": MetricsProfile(
            metrics={
                "net_interest_income": MetricSpec(
                    synonyms=["Чистый процентный доход"]
                ),
                "net_fee_income": MetricSpec(
                    synonyms=["Чистый комиссионный доход"]
                ),
                "net_income": MetricSpec(synonyms=["Чистая прибыль"]),
                "total_assets": MetricSpec(synonyms=["Итого активы"]),
                "total_equity": MetricSpec(
                    synonyms=["Итого собственный капитал"]
                ),
            },
            reporting_priority=["IFRS", "RSBU", "ISSUER"],
        ),
    }
)

_TICKERS_CONFIG = TickersConfig(
    tickers=[
        TickerEntry(
            ticker="SBER",
            e_disclosure_id="1",
            name="Sberbank",
            profile="non_bank",  # tests default to non_bank for shared fixtures
        )
    ]
)


@dataclass
class _FakeLLM:
    """LLMProvider stub. ``handler`` builds a response from each request."""

    handler: Callable[[LLMRequest], dict[str, Any] | Exception]
    name: str = "fake"
    supports_pdf_input: bool = False
    calls: list[LLMRequest] = field(default_factory=list)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        outcome = self.handler(req)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(
            data=outcome,
            raw_text=json.dumps(outcome, ensure_ascii=False),
            provider=self.name,
            model="m",
            input_tokens=10,
            output_tokens=5,
        )


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Database, Path, Path]:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    processed_dir.mkdir()
    with closing(db.connect()) as conn:
        TickersRepo(db, conn).upsert_from_config(
            [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
        )
    return db, raw_dir, processed_dir


def _seed_publication(
    db: Database,
    *,
    pub_id: str,
    standards: list[str | None],
    text_extracts: list[dict[str, Any] | None] | None = None,
    is_machine_readable: list[int] | None = None,
    raw_dir: Path | None = None,
    processed_dir: Path | None = None,
) -> str:
    """Seed a publication with N documents and return the publication id.

    ``standards[i]`` controls each document's reporting_standard;
    ``text_extracts[i]`` (optional) writes the JSON for that doc and points
    documents.text_extract_path at it.
    """
    with closing(db.connect()) as conn:
        pubs = PublicationsRepo(db, conn)
        docs = DocumentsRepo(db, conn)
        pubs.upsert_discovered(
            publication_id=pub_id,
            ticker="SBER",
            publication_type="report",
            publication_date="2026-04-01",
            source_url="https://example.test/r.zip",
        )
        for status in ("downloaded", "unpacked", "classified", "extracted"):
            pubs.mark_status(pub_id, status)  # type: ignore[arg-type]

        for i, std in enumerate(standards):
            rel = f"_unpacked/doc{i}.pdf"
            if raw_dir is not None:
                full = raw_dir / "SBER" / pub_id / rel
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_bytes(b"%PDF-fake")
            docs.add_documents(
                pub_id,
                [DocumentInput(relative_path=rel, file_hash=f"h{i}", mime_type="application/pdf")],
            )
            doc_id = [
                d.document_id
                for d in docs.list_for_publication(pub_id)
                if d.relative_path == rel
            ][0]
            mr = (is_machine_readable or [1] * len(standards))[i]
            docs.update_classification(
                doc_id,
                reporting_standard=std,  # type: ignore[arg-type]
                report_form="balance_sheet" if std else None,
                is_machine_readable=bool(mr) if mr is not None else None,
                page_count=1,
            )
            if text_extracts and processed_dir is not None and text_extracts[i]:
                rel_path = Path("SBER") / pub_id / f"{doc_id}.json"
                target = processed_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(text_extracts[i], ensure_ascii=False),
                    encoding="utf-8",
                )
                docs.set_text_extract_path(doc_id, str(rel_path))
    return pub_id


def _build_service(
    db: Database,
    raw_dir: Path,
    processed_dir: Path,
    llm: _FakeLLM,
    *,
    completeness_threshold: float = 0.5,
    tickers_config: TickersConfig = _TICKERS_CONFIG,
) -> tuple[MetricExtractorService, object]:
    conn = db.connect()
    service = MetricExtractorService(
        llm,  # type: ignore[arg-type]
        PublicationsRepo(db, conn),
        DocumentsRepo(db, conn),
        MetricsRepo(db, conn),
        metrics_config=_METRICS_CONFIG,
        tickers_config=tickers_config,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        max_tokens=2048,
        temperature=0.0,
        completeness_threshold=completeness_threshold,
    )
    return service, conn


def _full_extraction_payload(
    *,
    standard: str = "IFRS",
    unit: str = "ones",
) -> dict[str, Any]:
    return {
        "extractions": [
            {
                "reporting_date": "2025-12-31",
                "period_type": "FY",
                "reporting_standard": standard,
                "currency": "RUB",
                "unit": unit,
                "metrics": {
                    "revenue": {"value": 1500.0, "source_quote": "Revenue 1500"},
                    "ebitda": {"value": 600.0, "source_quote": "EBITDA 600"},
                    "net_income": {"value": 200.0, "source_quote": "NI 200"},
                    "total_assets": {"value": 9000.0, "source_quote": "Assets 9000"},
                    "total_debt": {"value": 3000.0, "source_quote": "Debt 3000"},
                },
            }
        ]
    }


# ---------------- Normalization ----------------


def test_normalize_thousands_to_ones() -> None:
    assert normalize_value(1500, "thousands", "ones") == 1_500_000


def test_normalize_millions_to_ones() -> None:
    assert normalize_value(2.5, "millions", "ones") == 2_500_000


def test_normalize_none_passes_through() -> None:
    assert normalize_value(None, "thousands", "ones") is None


# ---------------- Priority ----------------


@pytest.mark.asyncio
async def test_ifrs_chosen_over_rsbu(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db,
        pub_id="pub-1",
        standards=["RSBU", "IFRS"],
        raw_dir=raw_dir,
    )

    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["user_text"] = req.user_text
        return _full_extraction_payload()

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        docs = DocumentsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()

    primary = [d for d in docs if d.is_primary_for_period == 1]
    assert len(primary) == 1
    assert primary[0].reporting_standard == "IFRS"
    assert "IFRS" in captured["user_text"]
    assert outcomes[0].rows_written == 5


@pytest.mark.asyncio
async def test_rsbu_used_when_no_ifrs(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["RSBU"], raw_dir=raw_dir
    )
    llm = _FakeLLM(handler=lambda r: _full_extraction_payload(standard="RSBU"))
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_no_eligible_doc_marks_skipped_without_llm(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["OTHER"], raw_dir=raw_dir
    )

    def handler(req: LLMRequest) -> dict[str, Any]:
        raise AssertionError("LLM must not be called")

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        pub_after = PublicationsRepo(db, conn).get_by_id(pub_id)
    finally:
        conn.close()

    assert llm.calls == []
    assert pub_after is not None and pub_after.status == "skipped"
    assert outcomes[0].skipped_reason is not None


# ---------------- Repo writes ----------------


@pytest.mark.asyncio
async def test_writes_metric_rows(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["IFRS"], raw_dir=raw_dir
    )
    llm = _FakeLLM(handler=lambda r: _full_extraction_payload())
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        rows = MetricsRepo(db, conn).list_for_publication(pub_id)
        pub_after = PublicationsRepo(db, conn).get_by_id(pub_id)
    finally:
        conn.close()

    by_name = {r.metric_name: r for r in rows}
    assert {"revenue", "ebitda", "net_income", "total_assets", "total_debt"}.issubset(
        by_name
    )
    assert by_name["revenue"].value == 1500.0
    assert by_name["revenue"].currency == "RUB"
    assert pub_after is not None and pub_after.is_incomplete == 0


@pytest.mark.asyncio
async def test_idempotent_second_run_replaces_rows(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["IFRS"], raw_dir=raw_dir
    )
    llm = _FakeLLM(handler=lambda r: _full_extraction_payload())
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        await service.run([pub])
        rows = MetricsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()

    # Two runs over the same publication still produce 5 metric rows
    # (replace_for_publication clears + re-inserts).
    assert len(rows) == 5


# ---------------- Errors ----------------


@pytest.mark.asyncio
async def test_invalid_response_marks_publication_failed_other_continue(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_a = _seed_publication(
        db, pub_id="pub-a", standards=["IFRS"], raw_dir=raw_dir
    )
    pub_b = _seed_publication(
        db, pub_id="pub-b", standards=["IFRS"], raw_dir=raw_dir
    )

    def handler(req: LLMRequest) -> dict[str, Any]:
        # First publication gets an invalid payload; second gets a good one.
        if "pub-a" in req.user_text or len(handler_state) == 0:
            handler_state.append(1)
            return {"extractions": "not-an-array"}
        return _full_extraction_payload()

    handler_state: list[int] = []
    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pubs_repo = PublicationsRepo(db, conn)
        pa = pubs_repo.get_by_id(pub_a)
        pb = pubs_repo.get_by_id(pub_b)
        assert pa is not None and pb is not None
        outcomes = await service.run([pa, pb])
        pa_after = pubs_repo.get_by_id(pub_a)
        pb_after = pubs_repo.get_by_id(pub_b)
    finally:
        conn.close()

    # Only pub-b made it through.
    assert len(outcomes) == 1
    assert outcomes[0].publication_id == pub_b
    assert pa_after is not None and pa_after.status == "failed"
    assert pb_after is not None and pb_after.status == "extracted"


# ---------------- Completeness ----------------


@pytest.mark.asyncio
async def test_low_coverage_marks_incomplete(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["IFRS"], raw_dir=raw_dir
    )

    def handler(req: LLMRequest) -> dict[str, Any]:
        return {
            "extractions": [
                {
                    "reporting_date": "2025-12-31",
                    "period_type": "FY",
                    "reporting_standard": "IFRS",
                    "currency": "RUB",
                    "unit": "ones",
                    "metrics": {
                        # Only 2 of 5 filled.
                        "revenue": {"value": 100.0, "source_quote": "r"},
                        "ebitda": {"value": None, "source_quote": None},
                        "net_income": {"value": 25.0, "source_quote": "n"},
                        "total_assets": {"value": None, "source_quote": None},
                        "total_debt": {"value": None, "source_quote": None},
                    },
                }
            ]
        }

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(
        db, raw_dir, processed_dir, llm, completeness_threshold=0.5
    )
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        pub_after = PublicationsRepo(db, conn).get_by_id(pub_id)
    finally:
        conn.close()

    assert outcomes[0].is_incomplete is True
    assert outcomes[0].coverage_ratio == pytest.approx(2 / 5)
    assert pub_after is not None and pub_after.is_incomplete == 1


@pytest.mark.asyncio
async def test_unit_normalization_writes_absolute_value(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["IFRS"], raw_dir=raw_dir
    )
    llm = _FakeLLM(handler=lambda r: _full_extraction_payload(unit="thousands"))
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        rows = MetricsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()

    by_name = {r.metric_name: r for r in rows}
    # 1500 thousands × 1000 / 1 (ones) = 1_500_000
    assert by_name["revenue"].value == 1_500_000


# ---------------- Patch 19 — profile + only_in_sources + aggregation -----


@pytest.mark.asyncio
async def test_bank_profile_prompt_carries_bank_metrics_only(
    workspace: tuple[Database, Path, Path],
) -> None:
    """SBER as a bank: prompt must contain net_interest_income, NOT revenue/ebitda."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["IFRS"], raw_dir=raw_dir
    )

    bank_tickers = TickersConfig(
        tickers=[
            TickerEntry(
                ticker="SBER",
                e_disclosure_id="1",
                name="Sberbank",
                profile="bank",
            )
        ]
    )

    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["system"] = req.system
        return {
            "extractions": [
                {
                    "reporting_date": "2025-12-31",
                    "period_type": "FY",
                    "reporting_standard": "IFRS",
                    "currency": "RUB",
                    "unit": "ones",
                    "metrics": {
                        "net_interest_income": {"value": 100.0, "source_quote": "NII 100"},
                        "net_fee_income": {"value": 50.0, "source_quote": "NF 50"},
                        "net_income": {"value": 30.0, "source_quote": "NI 30"},
                        "total_assets": {"value": 1000.0, "source_quote": "TA 1000"},
                        "total_equity": {"value": 200.0, "source_quote": "TE 200"},
                    },
                }
            ]
        }

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(
        db, raw_dir, processed_dir, llm, tickers_config=bank_tickers
    )
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()

    assert "net_interest_income" in captured["system"]
    assert "net_fee_income" in captured["system"]
    # Bank prompt must NOT mention non-bank metrics.
    assert "revenue" not in captured["system"].lower().split()
    assert "EBITDA" not in captured["system"]


@pytest.mark.asyncio
async def test_non_bank_profile_prompt_carries_corp_metrics(
    workspace: tuple[Database, Path, Path],
) -> None:
    """Default non_bank profile: prompt mentions revenue / EBITDA / total_debt."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["IFRS"], raw_dir=raw_dir
    )
    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["system"] = req.system
        return _full_extraction_payload()

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()

    assert "revenue" in captured["system"]
    assert "ebitda" in captured["system"]
    assert "Заемные средства" in captured["system"]


@pytest.mark.asyncio
async def test_rsbu_source_drops_ebitda_from_prompt_and_completeness(
    workspace: tuple[Database, Path, Path],
) -> None:
    """Patch 19: RSBU document — ebitda is filtered out of the prompt AND
    excluded from completeness so a missing one doesn't penalise coverage."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["RSBU"], raw_dir=raw_dir
    )

    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["system"] = req.system
        # LLM responds without ebitda — Service must not request/store it.
        return {
            "extractions": [
                {
                    "reporting_date": "2025-12-31",
                    "period_type": "FY",
                    "reporting_standard": "RSBU",
                    "currency": "RUB",
                    "unit": "ones",
                    "metrics": {
                        "revenue": {"value": 1.0, "source_quote": "r"},
                        "net_income": {"value": 1.0, "source_quote": "n"},
                        "total_assets": {"value": 1.0, "source_quote": "ta"},
                        "total_debt": {"value": 1.0, "source_quote": "td"},
                    },
                }
            ]
        }

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        rows = MetricsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()

    # ebitda dropped from the prompt entirely.
    assert "ebitda" not in captured["system"].lower()
    # Only 4 applicable metrics for RSBU (no ebitda) — all 4 returned →
    # full coverage, not penalised for the missing IFRS-only metric.
    assert outcomes[0].requested_count == 4
    assert outcomes[0].extracted_count == 4
    assert outcomes[0].coverage_ratio == 1.0
    assert outcomes[0].is_incomplete is False
    by_name = {r.metric_name for r in rows}
    assert "ebitda" not in by_name


@pytest.mark.asyncio
async def test_rsbu_source_includes_total_debt_aggregation_hint(
    workspace: tuple[Database, Path, Path],
) -> None:
    """Patch 19: aggregation_hint for total_debt is injected only on RSBU."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["RSBU"], raw_dir=raw_dir
    )

    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["system"] = req.system
        return {
            "extractions": [
                {
                    "reporting_date": "2025-12-31",
                    "period_type": "FY",
                    "reporting_standard": "RSBU",
                    "currency": "RUB",
                    "unit": "ones",
                    "metrics": {
                        "revenue": {"value": 1.0, "source_quote": "r"},
                        "net_income": {"value": 1.0, "source_quote": "n"},
                        "total_assets": {"value": 1.0, "source_quote": "ta"},
                        "total_debt": {"value": 1.0, "source_quote": "td"},
                    },
                }
            ]
        }

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()

    assert "1410+1510" in captured["system"]


@pytest.mark.asyncio
async def test_ifrs_source_does_not_inject_rsbu_hint(
    workspace: tuple[Database, Path, Path],
) -> None:
    """Aggregation hint must not appear when source is IFRS."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db, pub_id="pub-1", standards=["IFRS"], raw_dir=raw_dir
    )
    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["system"] = req.system
        return _full_extraction_payload()

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()

    assert "1410+1510" not in captured["system"]


# ---------------- Patch 21 — ISSUER as third-priority source -----------


def _issuer_text_extract() -> dict[str, Any]:
    """Synthetic Issuer Report-shaped text payload with a real section 1.4."""
    return {
        "extraction_method": "native",
        "extracted_at": "2026-01-01T00:00:00+00:00",
        "pages": [
            {
                "page_number": 1,
                "text": (
                    "Содержание\n"
                    "1.4 Основные финансовые показатели ........... 10\n"
                    "1.5 Дебиторская задолженность ............... 14\n"
                ),
            },
            {
                "page_number": 10,
                "text": (
                    "1.4. Основные финансовые показатели\n"
                    "1.4.1 Чистый процентный доход — 1 309 млрд руб.\n"
                    "1.4.2 Чистый комиссионный доход — 269 млрд руб.\n"
                ),
            },
            {
                "page_number": 14,
                "text": (
                    "1.5 Дебиторская задолженность\n"
                    "Эта строка не должна попасть в trimmed slice.\n"
                ),
            },
        ],
    }


@pytest.mark.asyncio
async def test_issuer_chosen_when_no_ifrs_no_rsbu(
    workspace: tuple[Database, Path, Path],
) -> None:
    """Patch 21: with only an ISSUER document available, MetricExtractor
    picks it (3rd-priority source) and writes ``reporting_standard='ISSUER'``."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db,
        pub_id="pub-issuer",
        standards=["ISSUER"],
        text_extracts=[_issuer_text_extract()],
        raw_dir=raw_dir,
        processed_dir=processed_dir,
    )

    def handler(req: LLMRequest) -> dict[str, Any]:
        return {
            "extractions": [
                {
                    "reporting_date": "2025-06-30",
                    "period_type": "H1",
                    "reporting_standard": "ISSUER",
                    "currency": "RUB",
                    "unit": "millions",
                    "metrics": {
                        "revenue": {"value": 100.0, "source_quote": "r"},
                        "ebitda": {"value": 30.0, "source_quote": "e"},
                        "net_income": {"value": 10.0, "source_quote": "ni"},
                        "total_assets": {"value": 5000.0, "source_quote": "ta"},
                        "total_debt": {"value": 2000.0, "source_quote": "td"},
                    },
                }
            ]
        }

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        rows = MetricsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()

    assert outcomes[0].rows_written == 5
    # Patch 21 — storage accepts ISSUER directly, no RSBU fallback.
    assert all(r.reporting_standard == "ISSUER" for r in rows)


@pytest.mark.asyncio
async def test_ifrs_beats_issuer_when_both_present(
    workspace: tuple[Database, Path, Path],
) -> None:
    """3-tier priority: IFRS available → ISSUER skipped."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db,
        pub_id="pub-mixed",
        standards=["IFRS", "ISSUER"],
        raw_dir=raw_dir,
    )
    llm = _FakeLLM(handler=lambda r: _full_extraction_payload())
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        docs = DocumentsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()

    primary = [d for d in docs if d.is_primary_for_period == 1]
    assert len(primary) == 1
    assert primary[0].reporting_standard == "IFRS"


@pytest.mark.asyncio
async def test_rsbu_beats_issuer_when_both_present(
    workspace: tuple[Database, Path, Path],
) -> None:
    """3-tier priority: ISSUER picked only when neither IFRS nor RSBU is there."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db,
        pub_id="pub-rsbu-issuer",
        standards=["RSBU", "ISSUER"],
        raw_dir=raw_dir,
    )
    llm = _FakeLLM(
        handler=lambda r: _full_extraction_payload(standard="RSBU")
    )
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        docs = DocumentsRepo(db, conn).list_for_publication(pub_id)
    finally:
        conn.close()

    primary = [d for d in docs if d.is_primary_for_period == 1]
    assert primary[0].reporting_standard == "RSBU"


@pytest.mark.asyncio
async def test_issuer_user_text_is_trimmed_to_section_1_4(
    workspace: tuple[Database, Path, Path],
) -> None:
    """Patch 21: ISSUER user_text drops everything outside section 1.4."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_publication(
        db,
        pub_id="pub-issuer-trim",
        standards=["ISSUER"],
        text_extracts=[_issuer_text_extract()],
        raw_dir=raw_dir,
        processed_dir=processed_dir,
    )

    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["user_text"] = req.user_text
        captured["system"] = req.system
        return {
            "extractions": [
                {
                    "reporting_date": "2025-06-30",
                    "period_type": "H1",
                    "reporting_standard": "ISSUER",
                    "currency": "RUB",
                    "unit": "millions",
                    "metrics": {
                        "revenue": {"value": 1.0, "source_quote": "r"},
                        "ebitda": {"value": 1.0, "source_quote": "e"},
                        "net_income": {"value": 1.0, "source_quote": "ni"},
                        "total_assets": {"value": 1.0, "source_quote": "ta"},
                        "total_debt": {"value": 1.0, "source_quote": "td"},
                    },
                }
            ]
        }

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()

    user_text = captured["user_text"]
    # Section 1.4 content is present...
    assert "1.4.1" in user_text
    assert "Чистый процентный доход" in user_text
    # ...and the next-section line ("Эта строка не должна попасть") is NOT.
    assert "не должна попасть" not in user_text
    # System prompt got the ISSUER nudge from _assemble_user_text caller.
    assert "раздел 1.4" in user_text  # nudge sits inside user_text body


@pytest.mark.asyncio
async def test_issuer_falls_back_to_full_text_on_no_anchor(
    workspace: tuple[Database, Path, Path],
) -> None:
    """If section 1.4 anchor is absent, the full document text is sent
    (graceful degradation, with an issuer_trim warning logged)."""
    db, raw_dir, processed_dir = workspace
    extract = {
        "extraction_method": "native",
        "extracted_at": "2026-01-01T00:00:00+00:00",
        "pages": [
            {"page_number": 1, "text": "no section 1.4 anchor anywhere here"},
        ],
    }
    pub_id = _seed_publication(
        db,
        pub_id="pub-no-anchor",
        standards=["ISSUER"],
        text_extracts=[extract],
        raw_dir=raw_dir,
        processed_dir=processed_dir,
    )

    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["user_text"] = req.user_text
        return {"extractions": []}

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()

    # Full text made it through (the only available content).
    assert "no section 1.4 anchor anywhere here" in captured["user_text"]
