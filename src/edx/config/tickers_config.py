"""Issuer registry: maps MOEX tickers to their e-disclosure identifiers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReportingPriority = Literal["IFRS", "RSBU"]


class TickerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1)
    e_disclosure_id: str = Field(min_length=1)
    inn: str | None = None
    ogrn: str | None = None
    name: str = Field(min_length=1)
    priority_override: list[ReportingPriority] | None = None


class TickersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tickers: list[TickerEntry] = Field(default_factory=list)
