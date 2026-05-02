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
    # Override the User-Agent header the scraper sends. When None (default)
    # the client sends ``edx/<version> (+e-disclosure-extractor)``. Some
    # sites' anti-bot rules block obvious bot UAs — set this to a real
    # browser string to bypass that.
    user_agent: str | None = None
    # Session cookies to send with every request. Useful for sites behind a
    # JavaScript anti-bot challenge (ServicePipe, Cloudflare, etc.): solve
    # the challenge once in a real browser, copy values from
    # devtools (Application → Cookies), paste here. Cookies expire and need
    # manual refresh; for a permanent fix use ``http_backend: playwright``
    # below — it launches a real Chromium that solves the challenge in-page
    # and reuses Chromium's TLS-fingerprint for every subsequent request.
    cookies: dict[str, str] = Field(default_factory=dict)
    # Patch 23: HTTP backend for the Discoverer + Downloader stages.
    # ``httpx`` (default) — stdlib TLS, fast, fails when ServicePipe
    # validates the JA3 fingerprint (the cookie/UA workaround above
    # eventually stops working on most live sites).
    # ``playwright`` — launches headless Chromium once per run, runs the
    # JS-challenge in-browser, and routes all requests through Chromium's
    # network stack so JA3 matches the cookies. Requires
    # ``pip install playwright && playwright install chromium``.
    http_backend: Literal["httpx", "playwright"] = "httpx"


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

    # Patch 18 — primary knob: a PDF page is "text" when ``pymupdf.get_text``
    # returns at least this many non-whitespace characters; otherwise it's a
    # "scan" and the Text Extractor will route it through OCR.
    min_text_chars_per_page: int = Field(default=50, ge=1)
    # Deprecated by Patch 18 (kept loadable for back-compat with older
    # ``app.yaml`` files; no longer consulted by the Classifier).
    min_text_chars: int = Field(default=400, ge=1)
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


class MetricExtractorConfig(BaseModel):
    """Metric Extractor stage knobs (Patch 29+)."""

    model_config = ConfigDict(extra="forbid")

    # Patch 29: above this scan-page ratio the document goes through the
    # text-extract path (our own hybrid OCR), not Anthropic native-PDF.
    # Anthropic vision fails to read numbers from Russian RSBU forms with
    # thin grid + signature overlay; default 0.10 still admits IFRS
    # reports with 1-2 cover-page scans.
    scan_ratio_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    # Patch 29: which reporting standards may be sent as native PDF.
    # Empirically only IFRS works reliably; RSBU/ISSUER always need
    # text-path. Operator can widen at their own risk.
    pdf_input_standards: tuple[
        Literal["IFRS", "RSBU", "ISSUER"], ...
    ] = ("IFRS",)
    # Patch 30: cap on the balance-anchor-trimmed RSBU slice. 200k
    # comfortably holds balance + P&L + capital changes + notes for any
    # real Russian issuer; raise only if the LLM complains the section
    # was cut mid-form.
    balance_trim_max_chars: int = Field(default=200_000, gt=0)
    # Patch 33: opt-in vision-fallback. When the first text-pass leaves
    # coverage below ``vision_fallback_threshold`` and the document has
    # scan pages, retry once with the LLM provider's native PDF input
    # pointed at *only* the scan pages. Default off — switch on after
    # a baseline run shows residual coverage to recover.
    vision_fallback_enabled: bool = False
    vision_fallback_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0
    )
    vision_fallback_max_pages: int = Field(default=12, ge=1, le=50)


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
    # Patch 21: Issuer Reports (type=5) are 50–100 pages of MD&A with KPIs
    # in section 1.4 only. The Metric Extractor trims to that section
    # before LLM dispatch; this caps the final slice so even an
    # un-anchored fallback can't blow the prompt budget.
    issuer_trim_max_chars: int = Field(default=30_000, ge=1)


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
    metric_extractor: MetricExtractorConfig = Field(default_factory=MetricExtractorConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    google_drive: GoogleDriveConfig = Field(default_factory=GoogleDriveConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    contact_email: str | None = None
