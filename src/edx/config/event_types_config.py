"""Material-event taxonomy (ТЗ §6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventTypeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None


class EventTypesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_types: list[EventTypeSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_other_fallback(self) -> EventTypesConfig:
        codes = {item.code for item in self.event_types}
        if self.event_types and "other" not in codes:
            raise ValueError(
                "event_types must include a fallback entry with code='other'"
            )
        return self
