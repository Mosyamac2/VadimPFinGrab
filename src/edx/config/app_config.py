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


class ClassifierConfig(BaseModel):
    """PDF Classifier knobs (ТЗ §7.1 п.4)."""

    model_config = ConfigDict(extra="forbid")

    # Minimum total characters across the first inspected pages required for a
    # PDF to be considered machine-readable. Below this, we treat it as a scan.
    min_text_chars: int = Field(default=400, ge=1)
    # Number of pages from the beginning of the PDF to sample.
    first_pages_to_inspect: int = Field(default=3, ge=1, le=20)


class OrchestratorConfig(BaseModel):
    """Orchestrator-level knobs (ТЗ §7).

    ``publication_concurrency`` caps the number of publications a parallel
    stage handles concurrently (currently honoured by the Downloader's
    ``asyncio.Semaphore``). The Orchestrator itself runs stages in sequence —
    the cap therefore controls within-stage fan-out, not cross-stage
    parallelism.
    """

    model_config = ConfigDict(extra="forbid")

    publication_concurrency: int = Field(default=4, ge=1, le=64)


class GoogleDriveConfig(BaseModel):
    """Excel mart replication target on Google Drive (ТЗ §10.4).

    When ``enabled=true`` and the OAuth credentials are present in ``.env``
    (``GOOGLE_OAUTH_CLIENT_ID`` / ``GOOGLE_OAUTH_CLIENT_SECRET`` /
    ``GOOGLE_OAUTH_REFRESH_TOKEN``), the Writer's Replicator stage uploads
    the Excel mart to ``folder_id`` under the same ``file_name`` so the
    public link does not change between runs.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    folder_id: str | None = None
    file_name: str = "e-disclosure.xlsx"
    archive: bool = False


class ValidatorConfig(BaseModel):
    """Validator stage thresholds (ТЗ §11)."""

    model_config = ConfigDict(extra="forbid")

    # Publications with a metric coverage ratio below this fraction are
    # flagged ``is_incomplete=1`` and surfaced to the operator's QA report.
    completeness_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class TextExtractorConfig(BaseModel):
    """Text Extractor stage knobs (ТЗ §7.1 п.5)."""

    model_config = ConfigDict(extra="forbid")

    # Hard cap on total characters per document. Anything above is truncated
    # with a structured warning; the LLM stage handles its own chunking.
    max_chars: int = Field(default=400_000, ge=1)
    extract_tables: bool = True
    # Header/footer recurrence detector: a line must appear on at least this
    # many pages to be eligible for stripping.
    header_footer_min_pages: int = Field(default=3, ge=2)


class AppConfig(BaseModel):
    """Top-level ``app.yaml``."""

    model_config = ConfigDict(extra="forbid")

    paths: AppPaths = Field(default_factory=AppPaths)
    schedule: AppSchedule = Field(default_factory=AppSchedule)
    mode: AppMode = Field(default_factory=AppMode)
    discoverer: DiscovererConfig = Field(default_factory=DiscovererConfig)
    downloader: DownloaderConfig = Field(default_factory=DownloaderConfig)
    unpacker: UnpackerConfig = Field(default_factory=UnpackerConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    text_extractor: TextExtractorConfig = Field(default_factory=TextExtractorConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    google_drive: GoogleDriveConfig = Field(default_factory=GoogleDriveConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    contact_email: str | None = None
