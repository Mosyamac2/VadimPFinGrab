"""Pydantic model for the LLM event response."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventExtractionResult(BaseModel):
    """Validated LLM output. The service performs business-rule fallbacks
    (unknown ``event_type`` → ``other``, missing dates → ``publication_date``)
    rather than failing validation here.
    """

    model_config = ConfigDict(extra="forbid")

    event_type: str
    event_date: str | None = None
    publication_date: str | None = None
    summary: str = ""
    key_params: dict[str, Any] = Field(default_factory=dict)
