"""Financial metrics catalogue split by issuer profile (Patch 19).

A single flat metrics list — as in the v1 schema — collapses banks and
non-banks into the same KPI set. In practice the two have almost
disjoint headline metrics: corporates publish revenue / EBITDA /
total_debt, banks publish net interest income / net fee income / total
equity. Patch 19 reflects this in the config:

- ``MetricsConfig.profiles`` is a dict keyed by ``"non_bank"`` / ``"bank"``.
- Each profile carries its own ``metrics`` dict and ``reporting_priority``.
- ``MetricSpec.only_in_sources`` lets an extractor / validator skip a
  metric when the chosen source standard isn't expected to publish it
  (e.g. EBITDA is an IFRS construct; in an RSBU document it is *not* a
  hole, just absent by definition).
- ``MetricSpec.aggregation_hint`` carries a free-form note injected into
  the LLM prompt only when the source is RSBU (where, e.g., total debt
  spans two balance-sheet lines that need to be summed).

Loader rejects the v1 flat shape (``metrics:`` at the top level) so an
operator running ``edx update`` against a stale ``metrics.yaml`` gets a
clear ``ConfigError`` instead of a silent extraction with the wrong
metric set.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReportingStandard = Literal["IFRS", "RSBU", "ISSUER"]
ProfileName = Literal["non_bank", "bank"]


class MetricSpec(BaseModel):
    """One canonical metric and the strings the LLM should look for."""

    model_config = ConfigDict(extra="forbid")

    synonyms: list[str] = Field(min_length=1)
    unit: str = Field(default="RUB", min_length=1)
    scale_hints: list[str] = Field(default_factory=list)
    # When set, the metric is meaningful only in those reporting standards.
    # ``None`` == universal (extracted from any standard). Used by the
    # Metric Extractor to filter the LLM prompt and by the Validator to
    # ignore the metric in completeness for sources where it cannot exist.
    only_in_sources: list[ReportingStandard] | None = None
    # Free-form hint injected into the LLM prompt. The Metric Extractor
    # only attaches it when the source standard is RSBU — the IFRS path
    # already publishes the aggregated form.
    aggregation_hint: str | None = None


class MetricsProfile(BaseModel):
    """A complete metric catalogue + source priority for one issuer profile."""

    model_config = ConfigDict(extra="forbid")

    metrics: dict[str, MetricSpec] = Field(min_length=1)
    reporting_priority: list[ReportingStandard] = Field(min_length=1)


class MetricsConfig(BaseModel):
    """Top-level ``metrics.yaml``: a profile per issuer kind."""

    model_config = ConfigDict(extra="forbid")

    profiles: dict[ProfileName, MetricsProfile]

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_flat_schema(cls, data: object) -> object:
        if isinstance(data, dict) and "profiles" not in data and (
            "metrics" in data or "reporting_priority" in data
        ):
            raise ValueError(
                "metrics.yaml uses the pre-Patch-19 flat schema "
                "(top-level 'metrics:' / 'reporting_priority:'). Move the "
                "list into 'profiles.non_bank' and add a 'profiles.bank' "
                "section — see the example in metrics.yaml.template."
            )
        return data

    @model_validator(mode="after")
    def _both_profiles_required(self) -> MetricsConfig:
        missing = {"non_bank", "bank"} - set(self.profiles)
        if missing:
            raise ValueError(
                f"metrics.yaml must define both profiles: missing {sorted(missing)}"
            )
        return self

    def for_profile(self, profile: ProfileName) -> MetricsProfile:
        try:
            return self.profiles[profile]
        except KeyError as exc:
            raise KeyError(
                f"unknown metrics profile {profile!r}; "
                f"available: {sorted(self.profiles)}"
            ) from exc
