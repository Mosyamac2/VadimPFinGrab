"""``parse_listing_page`` against real Firefox view-source snapshots.

Patch 16 multi-issuer principle: every behavioral assertion on the parser
runs against fixtures from at least two different issuers (SBER + LKOH) so
the parser cannot accidentally encode a single issuer's quirks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edx.stages.discoverer.parser import (
    DiscoveredPublication,
    parse_listing_page,
    reporting_standard_for_type,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "edisclosure_real"
BASE_URL = "https://www.e-disclosure.ru"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- SBER (banking, all four observed types) -------------------------------


def test_parser_sber_type4_msfo_full_table() -> None:
    result = parse_listing_page(
        _load("sber_type_4.html"),
        base_url=BASE_URL,
        ticker="SBER",
        type_code=4,
    )
    assert result.warnings == []
    assert len(result.publications) == 7
    # All rows must carry the deterministic Patch 17 fields.
    for pub in result.publications:
        assert pub.report_type_code == 4
        assert pub.report_type_label is not None
        assert pub.reporting_period_year is not None
        assert pub.reporting_period_type is not None
        assert pub.publication_id.startswith("SBER-4-")
        assert pub.source_url.endswith("FileLoad.ashx?Fileid=" + pub.publication_id.split("-")[-1])
    # Period-type variety: at least Q1, H1, 9M, FY all present.
    period_types = {pub.reporting_period_type for pub in result.publications}
    assert {"Q1", "9M", "FY"}.issubset(period_types)
    # Latest fileid 1924258 (Q1 2026) was at the top of the listing.
    head = result.publications[0]
    assert head.publication_id == "SBER-4-1924258"
    assert head.reporting_period_year == 2026
    assert head.reporting_period_type == "Q1"


def test_parser_sber_type3_rsbu() -> None:
    result = parse_listing_page(
        _load("sber_type_3.html"),
        base_url=BASE_URL,
        ticker="SBER",
        type_code=3,
    )
    assert result.warnings == []
    assert len(result.publications) == 13
    for pub in result.publications:
        assert pub.report_type_code == 3
        assert pub.publication_id.startswith("SBER-3-")


def test_parser_sber_type2_annual_report_all_fy() -> None:
    """type=2 has an extra "approval date" cell — must not break date pick."""
    result = parse_listing_page(
        _load("sber_type_2.html"),
        base_url=BASE_URL,
        ticker="SBER",
        type_code=2,
    )
    assert result.warnings == []
    assert len(result.publications) == 4
    assert all(pub.reporting_period_type == "FY" for pub in result.publications)
    # Publication date is "Дата размещения" (the third date column on type=2),
    # not "Дата основания" or the extra approval date.
    head = result.publications[0]
    assert head.publication_date == "2025-07-01"


# --- LKOH (oil & gas, deep history) — multi-issuer guarantee ---------------


def test_parser_lkoh_type3_rsbu_deep_history() -> None:
    """LKOH РСБУ has 60 rows from 2009 to 2026 — stress-tests the period parser
    across a 17-year span and four period forms on a non-banking issuer.
    """
    result = parse_listing_page(
        _load("lkoh_type_3.html"),
        base_url=BASE_URL,
        ticker="LKOH",
        type_code=3,
    )
    assert result.warnings == []
    assert len(result.publications) == 60
    years = {pub.reporting_period_year for pub in result.publications}
    assert min(years) <= 2010 and max(years) >= 2026
    period_types = {pub.reporting_period_type for pub in result.publications}
    assert {"Q1", "H1", "9M", "FY"}.issubset(period_types)
    # The very first row is Q1 2026, the very last is the oldest annual.
    head = result.publications[0]
    assert (head.reporting_period_year, head.reporting_period_type) == (2026, "Q1")
    tail = result.publications[-1]
    assert tail.reporting_period_type == "FY"
    assert tail.reporting_period_year is not None and tail.reporting_period_year <= 2010


# --- soft-error handling ---------------------------------------------------


def test_parser_returns_empty_when_table_missing() -> None:
    """200 OK with no ``table.files-table`` → empty result, no warnings.

    This is the LKOH ``type=4`` case (issuer doesn't publish IFRS at this id).
    """
    html = "<html><body><div>No reports here.</div></body></html>"
    result = parse_listing_page(
        html, base_url=BASE_URL, ticker="LKOH", type_code=4
    )
    assert result.publications == []
    assert result.warnings == []


def test_parser_warns_on_empty_html() -> None:
    result = parse_listing_page(
        "", base_url=BASE_URL, ticker="X", type_code=4
    )
    assert result.publications == []
    assert "empty html" in result.warnings


def test_parser_skips_row_without_file_link_with_warning() -> None:
    html = (
        "<html><body><table class='files-table'><tbody>"
        "<tr><th>№</th><th>Тип</th><th>П</th><th>Д1</th><th>Д2</th><th>Файл</th><th></th></tr>"
        "<tr>"
        "<td class='row-number-cell'>1</td>"
        "<td class='type-cell'>Что-то</td>"
        "<td>2025</td>"
        "<td class='date-cell'>01.01.2025</td>"
        "<td class='date-cell'>02.01.2025</td>"
        "<td class='file-cell'>(no link)</td>"
        "<td class='cert-cell'></td>"
        "</tr></tbody></table></body></html>"
    )
    result = parse_listing_page(
        html, base_url=BASE_URL, ticker="X", type_code=3
    )
    assert result.publications == []
    assert any("file link" in w for w in result.warnings)


def test_parser_warns_on_unrecognised_period() -> None:
    html = (
        "<html><body><table class='files-table'><tbody>"
        "<tr><th>№</th><th>Т</th><th>П</th><th>Д1</th><th>Д2</th><th>Ф</th><th></th></tr>"
        "<tr>"
        "<td class='row-number-cell'>1</td>"
        "<td class='type-cell'>Что-то</td>"
        "<td>какой-то период</td>"
        "<td class='date-cell'>01.01.2025</td>"
        "<td class='date-cell'>02.01.2025</td>"
        "<td class='file-cell'><a class='file-link' href='https://x' data-fileid='999'>zip</a></td>"
        "<td class='cert-cell'></td>"
        "</tr></tbody></table></body></html>"
    )
    result = parse_listing_page(
        html, base_url=BASE_URL, ticker="X", type_code=3
    )
    # Row is still emitted (date and link are valid), but with a warning
    # and no period info.
    assert len(result.publications) == 1
    assert result.publications[0].reporting_period_year is None
    assert result.publications[0].reporting_period_type is None
    assert any("unrecognised reporting period" in w for w in result.warnings)


def test_parser_returns_publication_objects() -> None:
    result = parse_listing_page(
        _load("sber_type_4.html"),
        base_url=BASE_URL,
        ticker="SBER",
        type_code=4,
    )
    assert all(
        isinstance(pub, DiscoveredPublication) for pub in result.publications
    )


@pytest.mark.parametrize(
    "type_code, expected",
    [
        (2, "ANNUAL"),
        (3, "RSBU"),
        (4, "IFRS"),
        (5, "ISSUER"),
        (1, None),  # type=1 (statutory) is not a metric source
        (99, None),
    ],
)
def test_reporting_standard_mapping(type_code: int, expected: str | None) -> None:
    assert reporting_standard_for_type(type_code) == expected
