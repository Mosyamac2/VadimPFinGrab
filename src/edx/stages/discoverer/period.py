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
    # "2025, 12 месяцев" — Issuer-Report-style annual period (type=5).
    # Treated as full year just like the bare-year form.
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+12\s+(?:мес\.?|месяцев|месяца)$",
            re.IGNORECASE,
        ),
        "FY",
    ),
    # "2021, 1 квартал" / "2021, I квартал" — issuer-report quarter labels
    # (year-first comma form, observed on type=5 listings).
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+(?:1|I)\s+квартал$",
            re.IGNORECASE,
        ),
        "Q1",
    ),
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+(?:2|II)\s+квартал$",
            re.IGNORECASE,
        ),
        "Q2",
    ),
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+(?:3|III)\s+квартал$",
            re.IGNORECASE,
        ),
        "Q3",
    ),
    (
        re.compile(
            r"^(?P<year>\d{4})[\s,]+(?:4|IV)\s+квартал$",
            re.IGNORECASE,
        ),
        "Q4",
    ),
    # "1 квартал 2025" / "I квартал 2025" — quarter-first form (older
    # listings). Kept alongside the year-first form above.
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


# Patch 32: search-mode rules for free-form labels — text taken from the
# <a> link text or its `title` attribute when the dedicated period cell
# was empty. These regexes are NOT anchored: they look for a recognisable
# fragment anywhere in a longer label like "Бухгалтерская отчётность за
# 2025 год".
#
# Year-/quarter-/half-agnostic by construction: every `\d{4}` matches any
# four-digit year and every `[1-4]` / `[12]` matches any quarter or half
# without numerical hardcoding. The narrative "за …" preposition is the
# essential anti-false-positive signal — without it a stray year inside
# unrelated text ("Информация о компании 2025") MUST NOT match.
_SEARCH_RULES: tuple[tuple[re.Pattern[str], PeriodType | None], ...] = (
    # "за 1 квартал 2026 года" / "за 3 квартал 2024 г." — note explicit
    # "года" alternative: \b doesn't sit between «д» and «а», so a bare
    # «год» pattern does not match the inflected «года» form.
    (
        re.compile(
            r"(?i)за\s+([1-4])\s+квартал\s+(?P<year>\d{4})"
            r"(?:\s+(?:года|год|г\.?))?\b",
        ),
        None,  # period_type encoded in group(1)
    ),
    # "за 1 полугодие 2025" / "за 2 полугодие 2024 года"
    (
        re.compile(
            r"(?i)за\s+([12])\s+полугодие\s+(?P<year>\d{4})"
            r"(?:\s+(?:года|год))?\b"
        ),
        None,  # period_type encoded in group(1)
    ),
    # "за 2025 год" / "за 2025 г." / "за 2025 года"
    (
        re.compile(
            r"(?i)за\s+(?P<year>\d{4})\s*(?:года|год|г\.?)\b"
        ),
        "FY",
    ),
    # "Бухгалтерская отчётность за 2025" — bare label without «год».
    # Tolerant of «отчетность» (е) and «отчётность» (ё).
    (
        re.compile(
            r"(?i)Бухгалт\w+\s+отч[её]тность\s+за\s+(?P<year>\d{4})\b"
        ),
        "FY",
    ),
)


def parse_reporting_period(value: str, *, type_code: int) -> ParsedPeriod | None:
    """Parse the "Отчётный период" / "Отчётный год" cell or a free-form label.

    Returns ``None`` for empty input or unrecognised forms — the caller logs
    a warning and skips the row.

    ``type_code`` is currently unused but accepted in the signature so the
    call-site (Discoverer service) can pass it without coupling to internals.

    Two-pass matching:
    1. ``_RULES`` (anchored ``^...$``): designed for the dedicated period
       cell ("2025", "2025, 6 месяцев", …) — these labels arrive cleaned
       up by the portal renderer and have no surrounding noise.
    2. ``_SEARCH_RULES`` (Patch 32, unanchored ``re.search``): for
       free-form text from the link label or its ``title`` attribute when
       the period cell was empty. Each rule requires the «за …» preposition
       (or «Бухгалтерская отчётность за …») as anti-false-positive — a
       stray year in unrelated text is not enough.
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
    # Patch 32: fall back to substring search for free-form labels.
    for pattern, fixed_type in _SEARCH_RULES:
        match = pattern.search(text)
        if match:
            year = int(match.group("year"))
            if fixed_type is not None:
                return ParsedPeriod(year=year, period_type=fixed_type)
            # Quarter / half number is in capture group 1.
            number = match.group(1)
            if "квартал" in pattern.pattern:
                return ParsedPeriod(year=year, period_type=f"Q{number}")  # type: ignore[arg-type]
            return ParsedPeriod(year=year, period_type=f"H{number}")  # type: ignore[arg-type]
    return None
