"""Pure parser tests against fixed HTML fixtures."""

from __future__ import annotations

from pathlib import Path

from edx.stages.discoverer.parser import (
    DiscoveredPublication,
    make_publication_id,
    parse_issuer_card,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "edisclosure"
BASE_URL = "https://www.e-disclosure.ru"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parser_extracts_reports_and_events_from_full_card() -> None:
    result = parse_issuer_card(
        _load("issuer_full.html"), base_url=BASE_URL, ticker="SBER"
    )
    assert result.warnings == []
    by_url = {p.source_url: p for p in result.publications}
    expected_urls = {
        f"{BASE_URL}/portal/files/12345/q4-2025-ifrs.pdf",
        f"{BASE_URL}/portal/files/12345/q3-2025-ifrs.pdf",
        f"{BASE_URL}/portal/files/12345/q2-2025-rsbu.pdf",
        f"{BASE_URL}/portal/messages/abc-001.html",
        f"{BASE_URL}/portal/messages/abc-002.html",
    }
    assert set(by_url) == expected_urls

    annual = by_url[f"{BASE_URL}/portal/files/12345/q4-2025-ifrs.pdf"]
    assert annual.publication_type == "report"
    assert annual.publication_date == "2026-03-15"
    assert annual.title == "МСФО за 2025 год (годовая)"

    div = by_url[f"{BASE_URL}/portal/messages/abc-001.html"]
    assert div.publication_type == "event"
    assert div.publication_date == "2026-04-05"
    assert div.title.startswith("Решение о выплате")


def test_parser_handles_iso_date_format() -> None:
    result = parse_issuer_card(
        _load("issuer_reports_only.html"), base_url=BASE_URL, ticker="GAZP"
    )
    assert result.warnings == []
    assert len(result.publications) == 1
    pub = result.publications[0]
    assert pub.publication_date == "2026-04-15"
    assert pub.publication_type == "report"


def test_parser_empty_card_warns() -> None:
    result = parse_issuer_card(
        _load("issuer_empty.html"), base_url=BASE_URL, ticker="LKOH"
    )
    assert result.publications == []
    assert any("publications-section" in w for w in result.warnings)


def test_parser_empty_html_string_warns() -> None:
    result = parse_issuer_card("", base_url=BASE_URL, ticker="X")
    assert result.publications == []
    assert "empty html" in result.warnings


def test_parser_drops_malformed_rows_with_warnings() -> None:
    result = parse_issuer_card(
        _load("issuer_with_bad_rows.html"), base_url=BASE_URL, ticker="ROSN"
    )
    # Only one row is well-formed enough to make it through.
    assert len(result.publications) == 1
    assert result.publications[0].source_url.endswith("/portal/messages/ok.html")
    assert result.publications[0].publication_date == "2026-02-02"
    # The other three rows produce warnings.
    assert len(result.warnings) >= 3
    joined = " | ".join(result.warnings)
    assert "without date" in joined
    assert "unparseable date" in joined
    assert "empty href" in joined


def test_publication_id_is_deterministic() -> None:
    pid1 = make_publication_id("https://example/a.pdf", "2026-01-01", "SBER")
    pid2 = make_publication_id("https://example/a.pdf", "2026-01-01", "SBER")
    assert pid1 == pid2
    assert pid1.startswith("SBER-2026-01-01-")
    pid_other = make_publication_id(
        "https://example/b.pdf", "2026-01-01", "SBER"
    )
    assert pid_other != pid1


def test_parser_returns_publication_objects() -> None:
    result = parse_issuer_card(
        _load("issuer_reports_only.html"), base_url=BASE_URL, ticker="GAZP"
    )
    assert all(isinstance(p, DiscoveredPublication) for p in result.publications)
