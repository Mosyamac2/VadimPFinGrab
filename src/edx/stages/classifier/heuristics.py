"""Pure heuristics for reporting standard / report form detection.

No I/O, no logging — call sites turn the return value into a structured event.
"""

from __future__ import annotations

from typing import Final, Literal

from edx.config import MetricsConfig

ReportingStandardWithOther = Literal[
    "IFRS", "RSBU", "OTHER", "ISSUER", "ANNUAL"
]
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
# Patch 21: markers for the issuer-report (Положение Банка России 454-П)
# format. Used as a "second opinion" only when ``report_type_code`` is
# absent (legacy publications from before Patch 17). The Discoverer
# normally pre-marks type=5 publications as ISSUER from the URL.
_ISSUER_MARKERS: Final[tuple[str, ...]] = (
    "ежеквартальный отчёт эмитента",
    "ежеквартальный отчет эмитента",
    "отчёт эмитента эмиссионных",
    "отчет эмитента эмиссионных",
    "1.4. основные финансовые показатели",
    "1.4 основные финансовые показатели",
    "основные показатели финансово-хозяйственной деятельности",
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
    """Score IFRS / RSBU / ISSUER markers in the text.

    Ties (or zero hits across all three) → ``OTHER``. ``metrics_config`` is
    accepted to keep the signature stable if future versions weight metric
    synonyms; the current implementation doesn't need it.

    Note (Patch 21): the Classifier service prefers the deterministic
    ``publications.report_type_code`` over this heuristic — heuristics
    here only act as a backup for legacy rows where ``report_type_code``
    is absent.
    """
    if not text:
        return "OTHER"
    haystack = text.lower()
    scores: dict[ReportingStandardWithOther, int] = {
        "IFRS": sum(haystack.count(m) for m in _IFRS_MARKERS),
        "RSBU": sum(haystack.count(m) for m in _RSBU_MARKERS),
        "ISSUER": sum(haystack.count(m) for m in _ISSUER_MARKERS),
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return "OTHER"
    # Strict majority — ``best`` must beat every competitor.
    for k, v in scores.items():
        if k != best and v >= scores[best]:
            return "OTHER"
    return best


_TYPE_CODE_TO_STANDARD: Final[dict[int, ReportingStandardWithOther]] = {
    2: "ANNUAL",
    3: "RSBU",
    4: "IFRS",
    5: "ISSUER",
}


def reporting_standard_for_type_code(
    type_code: int | None,
) -> ReportingStandardWithOther | None:
    """Patch 21: deterministic mapping from the listing ``type=`` URL param.

    The Discoverer (Patch 16) attaches ``type_code`` to every publication.
    The Classifier prefers this over text-based heuristics — see
    ``ClassifierService._classify_one``.
    """
    if type_code is None:
        return None
    return _TYPE_CODE_TO_STANDARD.get(type_code)


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
