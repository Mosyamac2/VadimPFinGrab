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
ReportingStandardWithOther = Literal["IFRS", "RSBU", "OTHER"]
ReportingStandard = Literal["IFRS", "RSBU"]
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
