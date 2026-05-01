"""Financial metrics catalogue and reporting-standard priorities (ТЗ §5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReportingStandard = Literal["IFRS", "RSBU"]
MetricUnit = Literal["ones", "thousands", "millions", "billions"]


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_name: str = Field(min_length=1)
    synonyms_ifrs: list[str] = Field(default_factory=list)
    synonyms_rsbu: list[str] = Field(default_factory=list)
    unit: MetricUnit = "ones"
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    formula: str | None = None


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[MetricSpec] = Field(default_factory=list)
    reporting_priority: list[ReportingStandard] = Field(default_factory=list)
