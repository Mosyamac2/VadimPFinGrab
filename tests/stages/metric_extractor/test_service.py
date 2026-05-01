"""MetricExtractorService — priority, normalization, mocked LLM."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from edx.config import MetricsConfig, MetricSpec, TickerEntry
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
    metrics=[
        MetricSpec(canonical_name="revenue"),
        MetricSpec(canonical_name="ebitda"),
        MetricSpec(canonical_name="net_income"),
        MetricSpec(canonical_name="total_assets"),
        MetricSpec(canonical_name="total_debt"),
    ],
    reporting_priority=["IFRS", "RSBU"],
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
) -> tuple[MetricExtractorService, object]:
    conn = db.connect()
    service = MetricExtractorService(
        llm,  # type: ignore[arg-type]
        PublicationsRepo(db, conn),
        DocumentsRepo(db, conn),
        MetricsRepo(db, conn),
        metrics_config=_METRICS_CONFIG,
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
