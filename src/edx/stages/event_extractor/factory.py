"""Factory for the Event Extractor stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.providers.llm import LLMProvider
from edx.stages.event_extractor.service import EventExtractorService
from edx.storage import DocumentsRepo, EventsRepo, PublicationsRepo


def build_event_extractor_service(
    settings: AppSettings,
    publications_repo: PublicationsRepo,
    documents_repo: DocumentsRepo,
    events_repo: EventsRepo,
    llm_provider: LLMProvider,
) -> EventExtractorService:
    return EventExtractorService(
        llm_provider=llm_provider,
        publications_repo=publications_repo,
        documents_repo=documents_repo,
        events_repo=events_repo,
        event_types_config=settings.event_types,
        raw_dir=settings.app.paths.raw_dir,
        processed_dir=settings.app.paths.processed_dir,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
    )
