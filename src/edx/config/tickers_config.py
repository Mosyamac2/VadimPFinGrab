"""Issuer registry: maps MOEX tickers to their e-disclosure identifiers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReportingPriority = Literal["IFRS", "RSBU", "ISSUER"]
ProfileName = Literal["non_bank", "bank"]


class TickerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1)
    e_disclosure_id: str = Field(min_length=1)
    inn: str | None = None
    ogrn: str | None = None
    name: str = Field(min_length=1)
    # Patch 19: drives the metric set used by the LLM extractor and the
    # Validator's completeness threshold. Defaults to ``non_bank`` so
    # legacy tickers.yaml files keep working — operators must mark banks
    # explicitly (SBER, VTBR, BSPB, TCSG, MBNK, SVCB, …).
    profile: ProfileName = "non_bank"
    priority_override: list[ReportingPriority] | None = None
    # Patch 34: opt-in per-ticker vision-only path. When true, the
    # Metric Extractor renders every page of the ticker's RSBU /
    # ISSUER documents to PNG and ships them as Anthropic image
    # content blocks instead of the text/PDF channels. Costs ~1500
    # vision-tokens per page — switch on only after Patches 29-33
    # have been validated and a specific ticker still won't yield
    # acceptable coverage. IFRS for the same ticker stays on the
    # standard PDF path. Globally killable via
    # app.metric_extractor.vision_only_global_disabled.
    use_vision_extraction: bool = False


class TickersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tickers: list[TickerEntry] = Field(default_factory=list)

    def find(self, ticker: str) -> TickerEntry | None:
        for entry in self.tickers:
            if entry.ticker == ticker:
                return entry
        return None
