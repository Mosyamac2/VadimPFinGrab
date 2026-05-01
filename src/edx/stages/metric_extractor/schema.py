"""Build a strict JSON Schema from :class:`MetricsConfig` (ТЗ §5)."""

from __future__ import annotations

from typing import Any

from edx.config import MetricsConfig

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
REPORTING_STANDARDS: tuple[str, ...] = ("IFRS", "RSBU")


def build_metric_extraction_schema(metrics_config: MetricsConfig) -> dict[str, Any]:
    """Return a deterministic JSON Schema matching the LLM contract.

    Output shape::

        {
            "extractions": [
                {
                    "reporting_date": "YYYY-MM-DD",
                    "period_type": "Q1|...|FY",
                    "reporting_standard": "IFRS|RSBU",
                    "currency": "RUB|USD|...",
                    "unit": "ones|thousands|millions|billions",
                    "metrics": {
                        "<canonical_name>": {
                            "value": <number|null>,
                            "source_quote": "<exact text from doc|null>"
                        },
                        ...
                    }
                }
            ]
        }
    """
    canonical_names = [m.canonical_name for m in metrics_config.metrics]
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
