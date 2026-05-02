"""Reporting period parser (Patch 16, ``edx.stages.discoverer.period``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from edx.stages.discoverer.period import parse_reporting_period

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "edisclosure_real"


# --- direct rules ----------------------------------------------------------


@pytest.mark.parametrize(
    "value, type_code, expected_year, expected_type",
    [
        ("2025", 2, 2025, "FY"),
        ("2024", 2, 2024, "FY"),
        ("2026, 3 месяца", 4, 2026, "Q1"),
        ("2025, 6 месяцев", 4, 2025, "H1"),
        ("2025, 9 месяцев", 3, 2025, "9M"),
        # Hypothetical alternations covered by the rule dict — no real
        # fixture yet, but the parser must not break on them when they
        # appear on a less-tracked issuer.
        ("1 квартал 2025", 5, 2025, "Q1"),
        ("I квартал 2025", 5, 2025, "Q1"),
        ("II квартал 2024", 5, 2024, "Q2"),
        ("I полугодие 2024", 5, 2024, "H1"),
    ],
)
def test_parse_reporting_period_known_forms(
    value: str, type_code: int, expected_year: int, expected_type: str
) -> None:
    parsed = parse_reporting_period(value, type_code=type_code)
    assert parsed is not None
    assert parsed.year == expected_year
    assert parsed.period_type == expected_type


def test_parse_reporting_period_normalises_nbsp_and_thin_space() -> None:
    # NBSP between number and unit, and stray double spaces.
    nbsp = " "
    thin = " "
    assert parse_reporting_period(
        f"2025,{nbsp}3{thin}месяца", type_code=4
    ) == parse_reporting_period("2025, 3 месяца", type_code=4)


def test_parse_reporting_period_returns_none_for_unknown() -> None:
    assert parse_reporting_period("какой-то мусор", type_code=4) is None
    assert parse_reporting_period("", type_code=4) is None
    assert parse_reporting_period("   ", type_code=4) is None


# --- programmatic coverage from the real fixtures --------------------------
#
# The real fixtures collectively cover 2009..2026 and all four observed
# period forms. Walking them programmatically guarantees the rule dict stays
# in step with the live site as fixtures evolve — adding a fixture
# automatically extends coverage without touching the test.


def _all_period_cells_from_fixtures() -> list[tuple[str, int]]:
    """Yield every (cell_text, type_code) from every real listing fixture."""
    fixtures = [
        ("sber_type_2.html", 2),
        ("sber_type_3.html", 3),
        ("sber_type_4.html", 4),
        ("lkoh_type_3.html", 3),
    ]
    out: list[tuple[str, int]] = []
    for name, type_code in fixtures:
        src = (FIXTURES / name).read_text(encoding="utf-8")
        # All real fixtures are Firefox view-source — the body's text is the
        # real markup string we then re-parse.
        outer = HTMLParser(src)
        body = outer.body
        if body is None:
            continue
        real = HTMLParser(body.text())
        table = real.css_first("table.files-table")
        if table is None:
            continue
        tbody = table.css_first("tbody") or table
        for row in tbody.css("tr"):
            cells = row.css("td")
            if len(cells) < 6:
                continue
            text = cells[2].text(strip=True)
            if text:
                out.append((text, type_code))
    return out


def test_period_parser_covers_every_real_fixture_cell() -> None:
    samples = _all_period_cells_from_fixtures()
    assert samples, "no period cells extracted — fixtures missing?"
    failures: list[str] = []
    seen_period_types: set[str] = set()
    seen_years: set[int] = set()
    for text, type_code in samples:
        parsed = parse_reporting_period(text, type_code=type_code)
        if parsed is None:
            failures.append(f"({type_code}) {text!r}")
        else:
            seen_period_types.add(parsed.period_type)
            seen_years.add(parsed.year)
    assert not failures, (
        "period parser failed to cover real-fixture cells:\n"
        + "\n".join(failures)
    )
    # Coverage assertions — adding a fixture with a new form will tighten
    # these naturally; if they ever regress, we know coverage shrank.
    assert {"Q1", "H1", "9M", "FY"} <= seen_period_types
    assert min(seen_years) <= 2010, "expected at least one 2010 or earlier"
    assert max(seen_years) >= 2026


# ---------------------------------------------------------------------------
# Patch 32: free-form label parsing (search-mode rules)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "year", [2009, 2015, 2018, 2023, 2026, 2030]
)
def test_short_fy_god_works_for_any_year(year: int) -> None:
    parsed = parse_reporting_period(f"за {year} год", type_code=3)
    assert parsed is not None
    assert parsed.year == year
    assert parsed.period_type == "FY"


@pytest.mark.parametrize("year", [2010, 2024, 2030])
def test_short_fy_g_dot_works_for_any_year(year: int) -> None:
    parsed = parse_reporting_period(f"за {year} г.", type_code=3)
    assert parsed is not None
    assert parsed.year == year
    assert parsed.period_type == "FY"


@pytest.mark.parametrize("year", [2011, 2020, 2030])
def test_fy_bare_label_works_for_any_year(year: int) -> None:
    parsed = parse_reporting_period(
        f"Бухгалтерская отчётность за {year}", type_code=3
    )
    assert parsed is not None
    assert parsed.year == year
    assert parsed.period_type == "FY"


@pytest.mark.parametrize("q", [1, 2, 3, 4])
@pytest.mark.parametrize("year", [2018, 2025, 2030])
def test_q_full_works_for_every_quarter_and_year(q: int, year: int) -> None:
    parsed = parse_reporting_period(
        f"за {q} квартал {year} года", type_code=3
    )
    assert parsed is not None
    assert parsed.year == year
    assert parsed.period_type == f"Q{q}"


@pytest.mark.parametrize("h", [1, 2])
@pytest.mark.parametrize("year", [2015, 2025, 2030])
def test_h_full_works_for_every_half_and_year(h: int, year: int) -> None:
    parsed = parse_reporting_period(
        f"за {h} полугодие {year}", type_code=3
    )
    assert parsed is not None
    assert parsed.year == year
    assert parsed.period_type == f"H{h}"


def test_existing_yyyy_12_months_still_parses() -> None:
    parsed = parse_reporting_period("2025, 12 месяцев", type_code=5)
    assert parsed is not None
    assert (parsed.year, parsed.period_type) == (2025, "FY")


def test_existing_yyyy_n_quartal_still_parses() -> None:
    parsed = parse_reporting_period("2025, 1 квартал", type_code=5)
    assert parsed is not None
    assert (parsed.year, parsed.period_type) == (2025, "Q1")


def test_old_long_form_still_parses() -> None:
    parsed = parse_reporting_period("1 квартал 2025", type_code=3)
    assert parsed is not None
    assert (parsed.year, parsed.period_type) == (2025, "Q1")


def test_no_match_returns_none() -> None:
    assert parse_reporting_period("Документы для общего собрания", type_code=2) is None


def test_bare_year_without_modifier_does_not_match() -> None:
    # "2025" alone matches via _RULES (FY) — that is desirable for the
    # "Отчётный период" cell. But "Информация о компании 2025" must NOT
    # be interpreted as FY: there's no «за», no «год», no quarter/half
    # marker — it's a free-form sentence with a stray year.
    assert (
        parse_reporting_period("Информация о компании 2025", type_code=3)
        is None
    )


def test_year_inside_iso_date_does_not_false_match() -> None:
    """Date ranges should not be picked up as FY/Quarter via search rules."""
    # No «за … год», no quarter/half — this must not match.
    parsed = parse_reporting_period(
        "период с 01.01.2025 по 31.03.2025", type_code=3
    )
    assert parsed is None


def test_word_boundary_after_year_no_false_match() -> None:
    """A 4-digit run inside a longer digit sequence must not match."""
    assert parse_reporting_period("за 20251231", type_code=3) is None


def test_year_outside_realistic_range_still_parses_no_validation() -> None:
    """Patch 32 doesn't validate year ranges — that's the Validator's
    job. A regex can't grow magic windows out of paranoia, otherwise it
    breaks in 2030 without a code change.
    """
    parsed = parse_reporting_period("за 1850 год", type_code=3)
    assert parsed is not None
    assert parsed.year == 1850
    assert parsed.period_type == "FY"
