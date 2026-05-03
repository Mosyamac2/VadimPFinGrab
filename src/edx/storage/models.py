"""Storage value objects (frozen dataclasses) — repository input/output types.

These mirror the SQL schema in ``migrations/0001_init.sql``. They are *not*
Pydantic models on purpose: they are internal repo plumbing, validated at the
SQL CHECK level rather than Python-side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

PublicationStatus = Literal[
    "discovered",
    "downloaded",
    "unpacked",
    "classified",
    "extracted",
    "validated",
    "written",
    "failed",
    "skipped",
]
ALLOWED_PUBLICATION_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "discovered",
        "downloaded",
        "unpacked",
        "classified",
        "extracted",
        "validated",
        "written",
        "failed",
        "skipped",
    }
)

PublicationType = Literal["report", "event"]
# Patch 21: storage now accepts ISSUER (and ANNUAL on documents) along
# with the original IFRS/RSBU/OTHER. The CHECK constraints on
# ``documents.reporting_standard`` and ``metrics.reporting_standard`` are
# widened by migration 0009; the Literals here mirror them.
ReportingStandardWithOther = Literal["IFRS", "RSBU", "OTHER", "ISSUER", "ANNUAL"]
ReportingStandard = Literal["IFRS", "RSBU", "ISSUER"]
PeriodType = Literal["Q1", "Q2", "Q3", "Q4", "H1", "H2", "9M", "FY"]
RunStatus = Literal["running", "succeeded", "failed", "partial"]
RunMode = Literal["update", "full_reload"]


@dataclass(frozen=True)
class TickerRow:
    ticker: str
    e_disclosure_id: str
    inn: str | None
    ogrn: str | None
    name: str
    added_at: str


@dataclass(frozen=True)
class PublicationRow:
    publication_id: str
    ticker: str
    publication_type: PublicationType
    publication_date: str
    source_url: str
    file_hash: str | None
    status: PublicationStatus
    last_error: str | None
    discovered_at: str
    updated_at: str
    is_incomplete: int = 0
    # Patch 17: report type / period taken deterministically from the listing
    # URL and the "Отчётный период" column. ``None`` for legacy rows and for
    # ``publication_type='event'``.
    report_type_code: int | None = None
    report_type_label: str | None = None
    reporting_period_year: int | None = None
    reporting_period_type: PeriodType | None = None


@dataclass(frozen=True)
class DocumentInput:
    """Inputs for ``DocumentsRepo.add_documents`` (no auto-id, no classification)."""

    relative_path: str
    file_hash: str
    mime_type: str | None = None


@dataclass(frozen=True)
class DocumentRow:
    document_id: int
    publication_id: str
    relative_path: str
    mime_type: str | None
    reporting_standard: ReportingStandardWithOther | None
    report_form: str | None
    is_machine_readable: int | None
    page_count: int | None
    file_hash: str
    is_primary_for_period: int = 0
    text_extract_path: str | None = None
    # Patch 18: per-page text/scan classification. ``pages_classification``
    # is a JSON array of ``{"page": int, "chars": int, "kind": "text"|"scan"}``
    # or ``None`` for documents classified before Patch 18. The aggregate
    # ``is_machine_readable`` above stays as a coarse signal (≥1 text page
    # → 1; otherwise 0) so legacy callers keep working.
    pages_classification: str | None = None
    text_pages_count: int | None = None
    scan_pages_count: int | None = None


@dataclass(frozen=True)
class MetricInput:
    ticker: str
    reporting_date: str
    period_type: PeriodType
    reporting_standard: ReportingStandard
    metric_name: str
    value: float | None
    currency: str
    unit: str
    source_document_id: int | None
    qa_warning: str | None = None


@dataclass(frozen=True)
class MetricRow:
    metric_id: int
    ticker: str
    reporting_date: str
    period_type: PeriodType
    reporting_standard: ReportingStandard
    metric_name: str
    value: float | None
    currency: str
    unit: str
    source_document_id: int | None
    qa_warning: str | None
    extracted_at: str


@dataclass(frozen=True)
class EventInput:
    ticker: str
    event_date: str
    publication_date: str
    event_type: str
    summary: str
    key_params_json: str | None
    source_url: str
    source_publication_id: str


@dataclass(frozen=True)
class EventRow:
    event_id: int
    ticker: str
    event_date: str
    publication_date: str
    event_type: str
    summary: str
    key_params_json: str | None
    source_url: str
    source_publication_id: str | None
    extracted_at: str


@dataclass(frozen=True)
class RunRow:
    run_id: int
    started_at: str
    finished_at: str | None
    status: RunStatus
    mode: RunMode
    stats_json: str | None
    error_summary: str | None
    excel_drive_file_id: str | None = None
    excel_drive_link: str | None = None


@dataclass(frozen=True)
class QAIssueRow:
    issue_id: int
    publication_id: str
    ticker: str
    code: str
    message: str
    created_at: str


# Patch 38: self-evolution loop. Tick — одна итерация над батчем из 3
# компаний из e-disclosure-companies.csv. Phase — текущая фаза тика;
# verdict — финальный результат (None пока тик не завершён).
EvolutionPhase = Literal[
    "baseline", "claude_code", "verdict", "done", "failed"
]
EvolutionVerdict = Literal[
    "ok",
    "neutral",
    "regression",
    "regression_tests",
    "regression_canary",
    "fail",
    "flaky",
    "give_up",
    "skipped_budget",
]
SkiplistReason = Literal["give_up", "manual_blacklist", "moex_overlap"]


@dataclass(frozen=True)
class EvolutionTickRow:
    """One self-evolution iteration over a batch of companies."""

    tick_id: int
    started_at: str
    finished_at: str | None
    phase: EvolutionPhase
    verdict: EvolutionVerdict | None
    batch_json: str
    snaps_before_json: str | None
    snaps_after_json: str | None
    verdicts_json: str | None
    claude_session: str | None
    claude_cost_usd: float | None
    claude_turns: int | None
    commit_sha: str | None
    bundle_path: str | None
    error_summary: str | None


@dataclass(frozen=True)
class EvolutionSkiplistEntry:
    company_id: str
    reason: SkiplistReason
    failure_count: int
    last_tick_id: int | None
    updated_at: str
