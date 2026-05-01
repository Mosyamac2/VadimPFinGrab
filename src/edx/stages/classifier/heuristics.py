"""Pure heuristics for reporting standard / report form detection.

No I/O, no logging — call sites turn the return value into a structured event.
"""

from __future__ import annotations

from typing import Final, Literal

from edx.config import MetricsConfig

ReportingStandardWithOther = Literal["IFRS", "RSBU", "OTHER"]
ReportForm = Literal[
    "balance_sheet",
    "income_statement",
    "cash_flow",
    "notes",
    "cover",
    "other",
]

# Markers from ТЗ §5.1 plus a few obvious siblings. All matched
# case-insensitively against text.lower().
_IFRS_MARKERS: Final[tuple[str, ...]] = (
    "мсфо",
    "ifrs",
    "international financial reporting",
    "международными стандартами",
    "consolidated",
    "группа",
    "group",
)
_RSBU_MARKERS: Final[tuple[str, ...]] = (
    "рсбу",
    "пбу",
    "форма по окуд",
    "приказ минфина",
    "положение по бухгалтерскому",
    "бухгалтерский баланс",
)

_REPORT_FORM_MARKERS: Final[
    tuple[tuple[ReportForm, tuple[str, ...]], ...]
] = (
    (
        "balance_sheet",
        (
            "бухгалтерский баланс",
            "балансовый отчёт",
            "балансовый отчет",
            "statement of financial position",
            "balance sheet",
        ),
    ),
    (
        "income_statement",
        (
            "отчёт о финансовых результатах",
            "отчет о финансовых результатах",
            "отчёт о прибылях и убытках",
            "отчет о прибылях и убытках",
            "income statement",
            "statement of profit or loss",
            "statement of comprehensive income",
        ),
    ),
    (
        "cash_flow",
        (
            "отчёт о движении денежных средств",
            "отчет о движении денежных средств",
            "cash flow statement",
            "statement of cash flows",
        ),
    ),
    (
        "notes",
        (
            "примечания к",
            "пояснения к",
            "notes to the financial statements",
            "notes to the consolidated",
        ),
    ),
    (
        "cover",
        (
            "титульный лист",
            "auditor's report",
            "аудиторское заключение",
            "независимый аудит",
        ),
    ),
)


def detect_reporting_standard(
    text: str,
    metrics_config: MetricsConfig | None = None,  # noqa: ARG001 — reserved for future synonym signals
) -> ReportingStandardWithOther:
    """Score IFRS vs RSBU markers in the text.

    Ties (or zero hits on both) → ``OTHER``. ``metrics_config`` is accepted to
    keep the signature stable if future versions weight metric-name synonyms;
    the current implementation does not need it.
    """
    if not text:
        return "OTHER"
    haystack = text.lower()
    ifrs_score = sum(haystack.count(m) for m in _IFRS_MARKERS)
    rsbu_score = sum(haystack.count(m) for m in _RSBU_MARKERS)
    if ifrs_score > rsbu_score and ifrs_score > 0:
        return "IFRS"
    if rsbu_score > ifrs_score and rsbu_score > 0:
        return "RSBU"
    return "OTHER"


def detect_report_form(text: str) -> ReportForm:
    """Pick the report form based on which marker family appears most often."""
    if not text:
        return "other"
    haystack = text.lower()
    best: ReportForm = "other"
    best_score = 0
    for form, markers in _REPORT_FORM_MARKERS:
        score = sum(haystack.count(m) for m in markers)
        if score > best_score:
            best = form
            best_score = score
    return best
