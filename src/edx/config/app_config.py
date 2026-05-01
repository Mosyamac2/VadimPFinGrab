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


class DiscovererConfig(BaseModel):
    """HTTP scraping behaviour for the Discoverer stage (ТЗ §2)."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://www.e-disclosure.ru"
    requests_per_second: float = Field(default=1.0, gt=0)
    request_timeout_s: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_min_wait_s: float = Field(default=0.5, ge=0)
    retry_max_wait_s: float = Field(default=10.0, gt=0)
    respect_robots: bool = True


class DownloaderConfig(BaseModel):
    """Downloader stage knobs (ТЗ §7.1 п.2)."""

    model_config = ConfigDict(extra="forbid")

    concurrency: int = Field(default=4, ge=1, le=64)
    follow_html_links: bool = True
    chunk_size_bytes: int = Field(default=64 * 1024, ge=1024)


class UnpackerConfig(BaseModel):
    """Unpacker stage safety limits (ТЗ §7.1 п.3)."""

    model_config = ConfigDict(extra="forbid")

    max_unpacked_mb: int = Field(default=500, ge=1)


class AppConfig(BaseModel):
    """Top-level ``app.yaml``."""

    model_config = ConfigDict(extra="forbid")

    paths: AppPaths = Field(default_factory=AppPaths)
    schedule: AppSchedule = Field(default_factory=AppSchedule)
    mode: AppMode = Field(default_factory=AppMode)
    discoverer: DiscovererConfig = Field(default_factory=DiscovererConfig)
    downloader: DownloaderConfig = Field(default_factory=DownloaderConfig)
    unpacker: UnpackerConfig = Field(default_factory=UnpackerConfig)
    contact_email: str | None = None
