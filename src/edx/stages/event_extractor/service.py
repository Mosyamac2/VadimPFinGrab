"""EventExtractorService: structures one publication's material event."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from edx.config import EventTypesConfig
from edx.logging_setup import get_logger
from edx.providers.llm import LLMProvider, LLMRequest, LLMUnavailableError
from edx.stages.event_extractor.html_to_text import html_to_text
from edx.stages.event_extractor.models import EventExtractionResult
from edx.stages.event_extractor.prompts import build_system_prompt
from edx.stages.event_extractor.schema import (
    SUMMARY_MAX_CHARS,
    build_event_extraction_schema,
)
from edx.storage import (
    DocumentRow,
    DocumentsRepo,
    EventInput,
    EventsRepo,
    PublicationRow,
    PublicationsRepo,
)

FALLBACK_EVENT_TYPE = "other"
HTML_SUFFIXES: tuple[str, ...] = (".html", ".htm")


@dataclass(frozen=True)
class EventExtractOutcome:
    publication_id: str
    event_type: str
    used_fallback_event_type: bool
    used_fallback_event_date: bool
    summary_truncated: bool
    skipped_reason: str | None = None


class EventExtractorService:
    """Drives one LLM call per event publication.

    Provider-agnostic: depends only on :class:`LLMProvider`. The events
    catalogue is read from :class:`EventTypesConfig`, so adding a new event
    type is a YAML edit.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        publications_repo: PublicationsRepo,
        documents_repo: DocumentsRepo,
        events_repo: EventsRepo,
        *,
        event_types_config: EventTypesConfig,
        raw_dir: Path,
        processed_dir: Path,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        self.llm_provider = llm_provider
        self.publications_repo = publications_repo
        self.documents_repo = documents_repo
        self.events_repo = events_repo
        self.event_types_config = event_types_config
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._allowed_codes = {item.code for item in event_types_config.event_types}
        self._json_schema = build_event_extraction_schema(event_types_config)
        self._system_prompt = build_system_prompt(event_types_config)
        self._log = get_logger("edx.stages.event_extractor")

    async def run(
        self, publications: Iterable[PublicationRow]
    ) -> list[EventExtractOutcome]:
        outcomes: list[EventExtractOutcome] = []
        for pub in publications:
            if pub.publication_type != "event":
                self._log.warning(
                    "event_extract_skip_non_event",
                    publication_id=pub.publication_id,
                    publication_type=pub.publication_type,
                )
                continue
            try:
                outcome = await self._extract_one(pub)
            except LLMUnavailableError as exc:
                self._log.error(
                    "event_extract_llm_unavailable",
                    publication_id=pub.publication_id,
                    error=str(exc),
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                continue
            except ValidationError as exc:
                self._log.error(
                    "event_extract_invalid_response",
                    publication_id=pub.publication_id,
                    error=str(exc),
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                continue
            except Exception as exc:  # noqa: BLE001 — fail-soft per ТЗ §14
                self._log.error(
                    "event_extract_failed",
                    publication_id=pub.publication_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                continue
            outcomes.append(outcome)
        return outcomes

    async def _extract_one(self, pub: PublicationRow) -> EventExtractOutcome:
        documents = self.documents_repo.list_for_publication(pub.publication_id)
        user_text = self._build_user_text(pub, documents)
        if not user_text.strip():
            self._log.warning(
                "event_extract_no_text",
                publication_id=pub.publication_id,
            )
            self.publications_repo.mark_status(
                pub.publication_id,
                "skipped",
                error="no readable text content for event publication",
            )
            return EventExtractOutcome(
                publication_id=pub.publication_id,
                event_type=FALLBACK_EVENT_TYPE,
                used_fallback_event_type=False,
                used_fallback_event_date=False,
                summary_truncated=False,
                skipped_reason="no text content",
            )

        request = LLMRequest(
            system=self._system_prompt,
            user_text=user_text,
            json_schema=self._json_schema,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            schema_name="extract_event",
            schema_description="Структурированное сообщение о существенном факте",
        )
        response = await self.llm_provider.complete(request)
        result = EventExtractionResult.model_validate(response.data)

        used_fallback_event_type = False
        if result.event_type not in self._allowed_codes:
            self._log.warning(
                "event_extract_unknown_event_type",
                publication_id=pub.publication_id,
                returned=result.event_type,
                fallback=FALLBACK_EVENT_TYPE,
            )
            event_type = FALLBACK_EVENT_TYPE
            used_fallback_event_type = True
        else:
            event_type = result.event_type

        used_fallback_event_date = False
        if not result.event_date:
            self._log.warning(
                "event_extract_event_date_missing",
                publication_id=pub.publication_id,
                fallback=pub.publication_date,
            )
            event_date = pub.publication_date
            used_fallback_event_date = True
        else:
            event_date = result.event_date

        publication_date = result.publication_date or pub.publication_date

        summary = result.summary or ""
        summary_truncated = False
        if len(summary) > SUMMARY_MAX_CHARS:
            summary = summary[:SUMMARY_MAX_CHARS]
            summary_truncated = True
            self._log.warning(
                "event_extract_summary_truncated",
                publication_id=pub.publication_id,
                max_chars=SUMMARY_MAX_CHARS,
            )

        key_params_json = (
            json.dumps(result.key_params, ensure_ascii=False)
            if result.key_params
            else None
        )

        self.events_repo.upsert_event(
            EventInput(
                ticker=pub.ticker,
                event_date=event_date,
                publication_date=publication_date,
                event_type=event_type,
                summary=summary,
                key_params_json=key_params_json,
                source_url=pub.source_url,
                source_publication_id=pub.publication_id,
            )
        )
        self.publications_repo.mark_status(pub.publication_id, "validated")

        self._log.info(
            "event_extract_completed",
            publication_id=pub.publication_id,
            event_type=event_type,
            used_fallback_event_type=used_fallback_event_type,
            used_fallback_event_date=used_fallback_event_date,
            summary_truncated=summary_truncated,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        return EventExtractOutcome(
            publication_id=pub.publication_id,
            event_type=event_type,
            used_fallback_event_type=used_fallback_event_type,
            used_fallback_event_date=used_fallback_event_date,
            summary_truncated=summary_truncated,
        )

    def _build_user_text(
        self,
        pub: PublicationRow,
        documents: list[DocumentRow],
    ) -> str:
        # Prefer pre-extracted text from the Text Extractor stage.
        for doc in documents:
            if not doc.text_extract_path:
                continue
            extract_path = self.processed_dir / doc.text_extract_path
            if not extract_path.is_file():
                continue
            try:
                payload = json.loads(extract_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            sections: list[str] = [
                f"Эмитент: {pub.ticker}.",
                f"Источник: {pub.source_url}",
            ]
            for page in payload.get("pages", []):
                page_text = (page.get("text") or "").strip()
                if not page_text:
                    continue
                sections.append(
                    f"--- page {page.get('page_number')} ---"
                )
                sections.append(page_text)
            joined = "\n".join(sections)
            if joined.strip():
                return joined

        # Fallback: HTML files saved by the Downloader.
        pub_dir = self.raw_dir / pub.ticker / pub.publication_id
        for doc in documents:
            full = pub_dir / doc.relative_path
            if not full.is_file():
                continue
            if full.suffix.lower() not in HTML_SUFFIXES:
                continue
            try:
                html = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            text = html_to_text(html)
            if not text:
                continue
            return (
                f"Эмитент: {pub.ticker}.\n"
                f"Источник: {pub.source_url}\n\n{text}"
            )

        return ""
