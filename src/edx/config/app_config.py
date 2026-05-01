"""Application-wide configuration: paths, schedule, mode."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RunMode = Literal["update", "full_reload"]


class AppPaths(BaseModel):
    """Locations of on-disk artefacts (mirrors ТЗ §10.1)."""

    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("data")
    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    state_db: Path = Path("data/state.sqlite")
    output_dir: Path = Path("output")
    excel_path: Path = Path("output/e-disclosure.xlsx")
    logs_dir: Path = Path("logs")


class AppSchedule(BaseModel):
    """Default cron-style schedule (used by deploy templates and docs)."""

    model_config = ConfigDict(extra="forbid")

    cron_time: str = "04:00"
    timezone: str = "Europe/Moscow"


class AppMode(BaseModel):
    """Runtime mode controls (incremental vs full-reload, backfill depth)."""

    model_config = ConfigDict(extra="forbid")

    backfill_years: int = Field(default=3, ge=1, le=20)
    default_run_mode: RunMode = "update"


class AppConfig(BaseModel):
    """Top-level ``app.yaml``."""

    model_config = ConfigDict(extra="forbid")

    paths: AppPaths = Field(default_factory=AppPaths)
    schedule: AppSchedule = Field(default_factory=AppSchedule)
    mode: AppMode = Field(default_factory=AppMode)
    contact_email: str | None = None
