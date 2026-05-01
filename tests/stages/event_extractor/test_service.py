"""EventExtractorService — fallbacks, idempotency, mocked LLM."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from edx.config import (
    EventTypesConfig,
    EventTypeSpec,
    TickerEntry,
)
from edx.providers.llm import LLMRequest, LLMResponse
from edx.stages.event_extractor.service import EventExtractorService
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    EventsRepo,
    PublicationsRepo,
    TickersRepo,
)

EVENT_TYPES = EventTypesConfig(
    event_types=[
        EventTypeSpec(code="dividends", display_name="Дивиденды"),
        EventTypeSpec(code="management_change", display_name="Смена менеджмента"),
        EventTypeSpec(code="m_and_a", display_name="M&A"),
        EventTypeSpec(code="other", display_name="Прочее"),
    ]
)


@dataclass
class _FakeLLM:
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


def _seed_event_publication(
    db: Database,
    raw_dir: Path,
    *,
    pub_id: str = "ev-1",
    publication_date: str = "2026-04-01",
    html_body: str | None = None,
    text_extract_json: dict[str, Any] | None = None,
    processed_dir: Path | None = None,
) -> str:
    pub_dir = raw_dir / "SBER" / pub_id
    pub_dir.mkdir(parents=True, exist_ok=True)
    with closing(db.connect()) as conn:
        pubs = PublicationsRepo(db, conn)
        pubs.upsert_discovered(
            publication_id=pub_id,
            ticker="SBER",
            publication_type="event",
            publication_date=publication_date,
            source_url=f"https://example.test/{pub_id}",
        )
        for status in ("downloaded", "unpacked", "classified", "extracted"):
            pubs.mark_status(pub_id, status)  # type: ignore[arg-type]
        docs = DocumentsRepo(db, conn)
        if html_body is not None:
            html_path = pub_dir / "index.html"
            html_path.write_text(html_body, encoding="utf-8")
            docs.add_documents(
                pub_id,
                [
                    DocumentInput(
                        relative_path="index.html",
                        file_hash="h-html",
                        mime_type="text/html",
                    )
                ],
            )
        if text_extract_json is not None and processed_dir is not None:
            docs.add_documents(
                pub_id,
                [
                    DocumentInput(
                        relative_path="_unpacked/event.pdf",
                        file_hash="h-pdf",
                        mime_type="application/pdf",
                    )
                ],
            )
            doc_id = next(
                d.document_id
                for d in docs.list_for_publication(pub_id)
                if d.relative_path.endswith("event.pdf")
            )
            extract_rel = Path("SBER") / pub_id / f"{doc_id}.json"
            target = processed_dir / extract_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(text_extract_json, ensure_ascii=False),
                encoding="utf-8",
            )
            docs.set_text_extract_path(doc_id, str(extract_rel))
    return pub_id


def _build_service(
    db: Database,
    raw_dir: Path,
    processed_dir: Path,
    llm: _FakeLLM,
) -> tuple[EventExtractorService, object]:
    conn = db.connect()
    service = EventExtractorService(
        llm,  # type: ignore[arg-type]
        PublicationsRepo(db, conn),
        DocumentsRepo(db, conn),
        EventsRepo(db, conn),
        event_types_config=EVENT_TYPES,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        max_tokens=2048,
        temperature=0.0,
    )
    return service, conn


def _good_dividends_payload() -> dict[str, Any]:
    summary = (
        "Наблюдательный совет рекомендовал утвердить дивиденды за "
        "2025 год в размере 22,50 руб. на акцию."
    )
    return {
        "event_type": "dividends",
        "event_date": "2026-04-28",
        "publication_date": "2026-04-28",
        "summary": summary,
        "key_params": {"per_share_rub": 22.5, "fiscal_year": "2025"},
    }


# ------------------------------------------------------------------ writes


@pytest.mark.asyncio
async def test_writes_event_row_from_html_publication(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_event_publication(
        db,
        raw_dir,
        html_body="<html><body><p>Дивиденды 22,50 руб.</p></body></html>",
    )
    llm = _FakeLLM(handler=lambda r: _good_dividends_payload())
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        ev = EventsRepo(db, conn).get_by_publication(pub_id)
        pub_after = PublicationsRepo(db, conn).get_by_id(pub_id)
    finally:
        conn.close()

    assert len(outcomes) == 1
    assert ev is not None
    assert ev.event_type == "dividends"
    assert ev.event_date == "2026-04-28"
    assert ev.summary.startswith("Наблюдательный совет")
    assert ev.key_params_json is not None
    assert json.loads(ev.key_params_json) == {
        "per_share_rub": 22.5,
        "fiscal_year": "2025",
    }
    assert pub_after is not None and pub_after.status == "validated"


@pytest.mark.asyncio
async def test_idempotent_repeat_run_does_not_duplicate(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_event_publication(
        db,
        raw_dir,
        html_body="<html><body>x</body></html>",
    )
    llm = _FakeLLM(handler=lambda r: _good_dividends_payload())
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        # Reset publication status to feed it through again.
        PublicationsRepo(db, conn).mark_status(pub_id, "extracted")
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE source_publication_id = ?",
            (pub_id,),
        ).fetchone()["c"]
    finally:
        conn.close()
    assert count == 1


@pytest.mark.asyncio
async def test_uses_text_extract_path_when_present(
    workspace: tuple[Database, Path, Path],
) -> None:
    """Service prefers Text Extractor JSON output over the raw HTML."""
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_event_publication(
        db,
        raw_dir,
        html_body="<html><body>HTML body</body></html>",
        text_extract_json={
            "extraction_method": "native",
            "extracted_at": "2026-05-01T00:00:00+00:00",
            "pages": [
                {
                    "page_number": 1,
                    "text": "Сообщение о дивидендах. Извлечено из PDF.",
                }
            ],
        },
        processed_dir=processed_dir,
    )

    captured: dict[str, str] = {}

    def handler(req: LLMRequest) -> dict[str, Any]:
        captured["user_text"] = req.user_text
        return _good_dividends_payload()

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
    finally:
        conn.close()

    assert "Сообщение о дивидендах" in captured["user_text"]
    assert "page 1" in captured["user_text"]


# ------------------------------------------------------------------ fallbacks


@pytest.mark.asyncio
async def test_unknown_event_type_falls_back_to_other(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_event_publication(
        db, raw_dir, html_body="<html><body>x</body></html>"
    )
    payload = _good_dividends_payload() | {"event_type": "alien_kind_not_in_catalogue"}
    llm = _FakeLLM(handler=lambda r: payload)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        ev = EventsRepo(db, conn).get_by_publication(pub_id)
    finally:
        conn.close()
    assert outcomes[0].used_fallback_event_type is True
    assert outcomes[0].event_type == "other"
    assert ev is not None and ev.event_type == "other"


@pytest.mark.asyncio
async def test_missing_event_date_falls_back_to_publication_date(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_event_publication(
        db,
        raw_dir,
        publication_date="2026-04-15",
        html_body="<html><body>x</body></html>",
    )
    payload = _good_dividends_payload() | {"event_date": None}
    llm = _FakeLLM(handler=lambda r: payload)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        ev = EventsRepo(db, conn).get_by_publication(pub_id)
    finally:
        conn.close()
    assert outcomes[0].used_fallback_event_date is True
    assert ev is not None and ev.event_date == "2026-04-15"


@pytest.mark.asyncio
async def test_long_summary_truncated_to_600_chars(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_event_publication(
        db, raw_dir, html_body="<html><body>x</body></html>"
    )
    long_summary = "А" * 1500
    payload = _good_dividends_payload() | {"summary": long_summary}
    llm = _FakeLLM(handler=lambda r: payload)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        outcomes = await service.run([pub])
        ev = EventsRepo(db, conn).get_by_publication(pub_id)
    finally:
        conn.close()
    assert outcomes[0].summary_truncated is True
    assert ev is not None and len(ev.summary) == 600


# ------------------------------------------------------------------ guards


@pytest.mark.asyncio
async def test_non_event_publications_skipped_without_llm(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    with closing(db.connect()) as conn:
        pubs = PublicationsRepo(db, conn)
        pubs.upsert_discovered(
            publication_id="rep-1",
            ticker="SBER",
            publication_type="report",
            publication_date="2026-04-01",
            source_url="https://x",
        )
        pubs.mark_status("rep-1", "extracted")

    def handler(req: LLMRequest) -> dict[str, Any]:
        raise AssertionError("LLM must not be called for report publications")

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id("rep-1")
        assert pub is not None
        outcomes = await service.run([pub])
    finally:
        conn.close()
    assert outcomes == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_no_text_marks_publication_skipped(
    workspace: tuple[Database, Path, Path],
) -> None:
    db, raw_dir, processed_dir = workspace
    pub_id = _seed_event_publication(db, raw_dir, html_body=None)

    def handler(req: LLMRequest) -> dict[str, Any]:
        raise AssertionError("LLM must not be called when there is no text")

    llm = _FakeLLM(handler=handler)
    service, conn = _build_service(db, raw_dir, processed_dir, llm)
    try:
        pub = PublicationsRepo(db, conn).get_by_id(pub_id)
        assert pub is not None
        await service.run([pub])
        pub_after = PublicationsRepo(db, conn).get_by_id(pub_id)
    finally:
        conn.close()
    assert pub_after is not None and pub_after.status == "skipped"
