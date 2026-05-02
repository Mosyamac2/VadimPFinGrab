"""Pydantic models for the parsed LLM response."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from edx.config.metrics_config import ReportingStandard
from edx.storage.models import PeriodType

# The LLM responds with one of these as ``unit`` (Patch 19 keeps the wire
# format identical; the conversion to ``ones`` happens in
# :mod:`edx.stages.metric_extractor.service`).
MetricUnit = Literal["ones", "thousands", "millions", "billions"]


class MetricExtractionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    source_quote: str | None = None


class PeriodExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reporting_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_type: PeriodType
    reporting_standard: ReportingStandard
    currency: str = Field(min_length=3, max_length=3)
    unit: MetricUnit
    metrics: dict[str, MetricExtractionItem]


class MetricExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extractions: list[PeriodExtraction] = Field(default_factory=list)


# Re-exported for convenience: helps tests import a single Literal.
__all__ = [
    "Literal",
    "MetricExtractionItem",
    "MetricExtractionResult",
    "PeriodExtraction",
]
