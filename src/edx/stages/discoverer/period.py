"""Reporting-period label parser for e-disclosure listing pages.

The "Отчётный период" / "Отчётный год" column on
``/portal/files.aspx?id=X&type=Y`` carries free-form Russian period labels.
This module turns them into ``(year, period_type)`` pairs that the rest of
the pipeline (Metric Extractor, Excel mart) can compare and group by.

Pure function, no I/O. Adding a new label form is a one-line addition to
``_RULES``; no parser code needs to change.

Forms observed on real listings (SBER ``id=3043`` types 2/3/4 + LKOH ``id=17``
type=3, snapshots in ``tests/fixtures/edisclosure_real/``):

- ``"2025"``                   → ``(2025, "FY")``  (annual reports)
- ``"2026, 3 месяца"``         → ``(2026, "Q1")``
- ``"2025, 6 месяцев"``        → ``(2025, "H1")``
- ``"2025, 9 месяцев"``        → ``(2025, "9M")``

Forms anticipated for older annual disclosures (covered by alternations
below, no real fixture yet — extend the rules dict when one appears):

- ``"1 квартал 2025"`` / ``"I квартал 2025"`` → ``(2025, "Q1")``
- ``"I полугодие 2024"``                     → ``(2024, "H1")``

NBSP (``\\u00a0``) and thin space (``\\u2009``) between the number and the
unit are normalised to a regular space before matching.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from edx.storage.models import PeriodType


@dataclass(frozen=True)
class ParsedPeriod:
    year: int
    period_type: PeriodType


# Normalise rare unicode whitespace to a regular space so the regexes below
# can stay readable.
_WS_NORMALISE = {
    ord(" "): " ",  # NBSP
    ord(" "): " ",  # THIN SPACE
    ord(" "): " ",  # NARROW NO-BREAK SPACE
}


def _normalise(value: str) -> str:
    text = unicodedata.normalize("NFC", value).translate(_WS_NORMALISE)
    return re.sub(r"\s+", " ", text).strip()


# Each rule is (regex, period_type). The regex must capture a single named
# group "year" with a 4-digit year. Order matters for ambiguous cases, but
# none of the current rules overlap.
_RULES: tuple[tuple[re.Pattern[str], PeriodType], ...] = (
    # "2025, 3 месяца" / "2025, 3 мес." / "2025 3 месяца"
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+3\s+(?:мес\.?|месяца|месяцев)$",
            re.IGNORECASE,
        ),
        "Q1",
    ),
    # "2025, 6 месяцев"
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+6\s+(?:мес\.?|месяцев|месяца)$",
            re.IGNORECASE,
        ),
        "H1",
    ),
    # "2025, 9 месяцев"
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+9\s+(?:мес\.?|месяцев|месяца)$",
            re.IGNORECASE,
        ),
        "9M",
    ),
    # "1 квартал 2025" / "I квартал 2025"
    (
        re.compile(
            r"^(?:1|I|первый)\s+квартал[\s,]+(?P<year>\d{4})$",
            re.IGNORECASE,
        ),
        "Q1",
    ),
    (
        re.compile(
            r"^(?:2|II|второй)\s+квартал[\s,]+(?P<year>\d{4})$",
            re.IGNORECASE,
        ),
        "Q2",
    ),
    (
        re.compile(
            r"^(?:3|III|третий)\s+квартал[\s,]+(?P<year>\d{4})$",
            re.IGNORECASE,
        ),
        "Q3",
    ),
    (
        re.compile(
            r"^(?:4|IV|четвертый|четвёртый)\s+квартал[\s,]+(?P<year>\d{4})$",
            re.IGNORECASE,
        ),
        "Q4",
    ),
    (
        re.compile(
            r"^(?:1|I|первое)\s+полугодие[\s,]+(?P<year>\d{4})$",
            re.IGNORECASE,
        ),
        "H1",
    ),
    (
        re.compile(
            r"^(?:2|II|второе)\s+полугодие[\s,]+(?P<year>\d{4})$",
            re.IGNORECASE,
        ),
        "H2",
    ),
    # Bare year — annual report. Must come last so it doesn't shadow the
    # quarter/half forms above.
    (re.compile(r"^(?P<year>\d{4})$"), "FY"),
)


def parse_reporting_period(value: str, *, type_code: int) -> ParsedPeriod | None:
    """Parse the "Отчётный период" / "Отчётный год" cell.

    Returns ``None`` for empty input or unrecognised forms — the caller logs
    a warning and skips the row.

    ``type_code`` is currently unused but accepted in the signature so the
    call-site (Discoverer service) can pass it without coupling to internals.
    Future rules may want to constrain interpretation per type
    (e.g. "year-only on type=2 means FY of that year, on type=3 may mean
    something else").
    """
    del type_code  # accepted for API symmetry; not used today
    text = _normalise(value)
    if not text:
        return None
    for pattern, period_type in _RULES:
        match = pattern.match(text)
        if match:
            return ParsedPeriod(
                year=int(match.group("year")),
                period_type=period_type,
            )
    return None
