"""Strict JSON Schema for one material-event extraction (ТЗ §6)."""

from __future__ import annotations

from typing import Any

from edx.config import EventTypesConfig

# 1–3 sentence summary; ТЗ §6.
SUMMARY_MAX_CHARS = 600


def build_event_extraction_schema(
    event_types_config: EventTypesConfig,
) -> dict[str, Any]:
    """Return the JSON Schema describing the LLM output for one event.

    The ``event_type`` enum is sourced directly from
    ``config/event_types.yaml`` — operators add types there without touching
    code. ``key_params`` is intentionally a free-form ``object`` with primitive
    values so the LLM can surface whatever numbers/strings are most relevant
    (sum of transaction, dividend size, %-share, etc.).
    """
    codes = [item.code for item in event_types_config.event_types]
    return {
        "type": "object",
        "properties": {
            "event_type": {"type": "string", "enum": codes},
            "event_date": {
                "type": ["string", "null"],
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
            },
            "publication_date": {
                "type": ["string", "null"],
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
            },
            "summary": {
                "type": "string",
                "maxLength": SUMMARY_MAX_CHARS,
            },
            "key_params": {
                "type": "object",
                "additionalProperties": {
                    "type": ["string", "number", "boolean", "null"]
                },
            },
        },
        "required": [
            "event_type",
            "event_date",
            "publication_date",
            "summary",
            "key_params",
        ],
        "additionalProperties": False,
    }
