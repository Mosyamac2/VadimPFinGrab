"""Build a strict JSON Schema from a :class:`MetricsProfile` (Patch 19).

The schema is parameterised by the (profile, source_standard) pair so an
RSBU document doesn't carry a ``"ebitda"`` slot the LLM would be tempted
to fill (it can't — RSBU doesn't publish EBITDA). Metrics with
``only_in_sources`` that exclude the chosen source are dropped from
``required`` and from ``properties``.
"""

from __future__ import annotations

from typing import Any

from edx.config import MetricsProfile, ReportingStandard

PERIOD_TYPES: tuple[str, ...] = (
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "H1",
    "H2",
    "9M",
    "FY",
)
UNITS: tuple[str, ...] = ("ones", "thousands", "millions", "billions")
REPORTING_STANDARDS: tuple[str, ...] = ("IFRS", "RSBU", "ISSUER")


def build_metric_extraction_schema(
    profile: MetricsProfile, *, source_standard: ReportingStandard
) -> dict[str, Any]:
    """Return a deterministic JSON Schema matching the LLM contract.

    Output shape per period::

        {
            "reporting_date": "YYYY-MM-DD",
            "period_type": "Q1|...|FY",
            "reporting_standard": "IFRS|RSBU|ISSUER",
            "currency": "RUB|USD|...",
            "unit": "ones|thousands|millions|billions",
            "metrics": {
                "<canonical_name>": {
                    "value": <number|null>,
                    "source_quote": "<exact text|null>"
                },
                ...
            }
        }
    """
    canonical_names = [
        name
        for name, spec in profile.metrics.items()
        if not spec.only_in_sources or source_standard in spec.only_in_sources
    ]
    metric_props: dict[str, Any] = {
        name: {
            "type": "object",
            "properties": {
                "value": {"type": ["number", "null"]},
                "source_quote": {"type": ["string", "null"]},
            },
            "required": ["value", "source_quote"],
            "additionalProperties": False,
        }
        for name in canonical_names
    }

    period_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reporting_date": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
            },
            "period_type": {"type": "string", "enum": list(PERIOD_TYPES)},
            "reporting_standard": {
                "type": "string",
                "enum": list(REPORTING_STANDARDS),
            },
            "currency": {
                "type": "string",
                "minLength": 3,
                "maxLength": 3,
            },
            "unit": {"type": "string", "enum": list(UNITS)},
            "metrics": {
                "type": "object",
                "properties": metric_props,
                "required": list(canonical_names),
                "additionalProperties": False,
            },
        },
        "required": [
            "reporting_date",
            "period_type",
            "reporting_standard",
            "currency",
            "unit",
            "metrics",
        ],
        "additionalProperties": False,
    }

    return {
        "type": "object",
        "properties": {
            "extractions": {
                "type": "array",
                "items": period_schema,
            }
        },
        "required": ["extractions"],
        "additionalProperties": False,
    }
