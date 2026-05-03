"""``issuer_trim.extract_section_1_4`` — synthetic anchors + real SBER PDF."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from edx.stages.text_extractor.issuer_trim import extract_section_1_4

REAL_FIXTURES = (
    Path(__file__).resolve().parents[2] / "fixtures" / "pdf" / "issuer"
)


# --- synthetic anchor variants -------------------------------------------


def test_label_finansovye_pokazateli() -> None:
    text = "...прочее\n1.4 Основные финансовые показатели\nстрока\n1.5 Дальше"
    out = extract_section_1_4(text, max_chars=10_000, min_section_chars=0)
    assert out.content is not None and "Основные финансовые показатели" in out.content
    assert out.end_anchor_seen == "1.5"
    assert out.anchor_label_seen == "Основные финансовые показатели"


def test_label_finansovo_economicheskie() -> None:
    text = "1.4. Основные финансово-экономические показатели\nстрока\n2. Сведения"
    out = extract_section_1_4(text, max_chars=10_000, min_section_chars=0)
    assert out.content is not None
    assert (
        out.anchor_label_seen
        == "Основные финансово-экономические показатели"
    )
    assert out.end_anchor_seen == "2."


def test_label_finansovo_hozyajstvennoj() -> None:
    text = (
        "1.4 Основные показатели финансово-хозяйственной деятельности\n"
        "строка\n"
        "1.5 Сведения"
    )
    out = extract_section_1_4(text, max_chars=10_000, min_section_chars=0)
    assert out.content is not None
    assert (
        out.anchor_label_seen
        == "Основные показатели финансово-хозяйственной деятельности"
    )


def test_handles_em_dash_in_label() -> None:
    """en-dash and em-dash in 'финансово-экономические' both match."""
    nbsp = " "
    text = (
        f"1.4{nbsp}Основные финансово—экономические показатели\n"
        "строка\n1.5"
    )
    out = extract_section_1_4(text, max_chars=10_000, min_section_chars=0)
    assert out.content is not None


def test_handles_unicode_spaces_around_1_4() -> None:
    """NBSP between 1.4 and the label is normalised."""
    nbsp = " "
    thin = " "
    text = f"1{thin}.{thin}4{nbsp}Основные{nbsp}финансовые{nbsp}показатели\nx\n1.5"
    out = extract_section_1_4(text, max_chars=10_000, min_section_chars=0)
    assert out.content is not None


def test_returns_none_when_no_anchor() -> None:
    out = extract_section_1_4("просто текст без раздела", max_chars=1000)
    assert out.content is None
    assert any("not found" in w for w in out.warnings)


def test_truncates_when_no_end_anchor() -> None:
    body = "x" * 5000
    text = f"1.4 Основные финансовые показатели\n{body}"
    out = extract_section_1_4(text, max_chars=200, min_section_chars=0)
    assert out.content is not None
    assert len(out.content) == 200
    assert out.end_anchor_seen is None
    assert any("end anchor not found" in w for w in out.warnings)


def test_picks_last_anchor_to_skip_TOC() -> None:
    """A TOC entry shouldn't capture the slice — real heading wins.

    Patch 35: TOC and the real anchor must sit > toc_distance_chars
    apart so the close-matches safeguard doesn't bail. Real reports
    typically have ~5-15k chars between TOC and the corresponding
    section heading, so 5000 chars of padding here is realistic.
    """
    body_padding = "ПОЯСНИТЕЛЬНАЯ ЗАПИСКА. " * 200  # ~5k chars
    text = (
        # TOC line.
        "Содержание\n1.4   Основные финансовые показатели ........... 10\n"
        "1.5   Сведения о размере дебиторской задолженности .... 14\n"
        + body_padding
        # Real section.
        + "1.4. Основные финансовые показатели\n"
        "1.4.1 Чистый процентный доход ... 1 309 млрд руб.\n"
        "1.5 Дебиторская задолженность\n"
    )
    out = extract_section_1_4(text, max_chars=10_000, min_section_chars=0)
    assert out.content is not None
    # Must capture the *real* section, not the TOC stub.
    assert "1.4.1" in out.content
    assert "Чистый процентный доход" in out.content
    assert any("matched 2 times" in w for w in out.warnings)


def test_empty_input_returns_none_with_warning() -> None:
    out = extract_section_1_4("", max_chars=1000)
    assert out.content is None
    assert "empty input text" in out.warnings


def test_max_chars_must_be_positive() -> None:
    with pytest.raises(ValueError):
        extract_section_1_4("x", max_chars=0)


# --- real SBER Issuer Report --------------------------------------------


def test_real_sber_issuer_report_section_1_4_extracted() -> None:
    """End-to-end: pull text from the SBER ОЭ 6м2025 PDF and assert the
    extractor lands on the real section 1.4 (not the TOC stub) with
    plausible KPI keywords inside."""
    pdf_path = REAL_FIXTURES / "sber_issuer_h1_2025.pdf"
    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    text = "\n".join((page.get_text("text") or "") for page in doc)
    doc.close()

    out = extract_section_1_4(text, max_chars=30_000)
    assert out.content is not None
    assert out.anchor_label_seen == "Основные финансовые показатели"
    assert out.end_anchor_seen in ("1.5", "2.")
    # The real section enumerates 1.4.1 .. 1.4.5 and mentions key
    # banking metrics by name.
    assert "1.4.1" in out.content
    assert "1.4.2" in out.content
    # Multi-match warning fires (TOC + real heading).
    assert any("matched" in w for w in out.warnings)


# --- parametric scan of any future issuer fixtures ----------------------


def _issuer_fixture_paths() -> list[Path]:
    return sorted(p for p in REAL_FIXTURES.glob("*.pdf"))


@pytest.mark.parametrize("pdf_path", _issuer_fixture_paths(), ids=lambda p: p.name)
def test_parametrize_real_issuer_reports(pdf_path: Path) -> None:
    """Drop a non-SBER Issuer Report into ``tests/fixtures/pdf/issuer/``
    and this test will pick it up automatically. If a new wording isn't
    covered by the regex alternations, it fails here with the actual
    document name — extend the regex with the real heading rather than
    by guess."""
    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    text = "\n".join((page.get_text("text") or "") for page in doc)
    doc.close()
    out = extract_section_1_4(text, max_chars=30_000)
    assert out.content is not None, (
        f"section 1.4 not found in {pdf_path.name} — extend "
        "ANCHOR_START in issuer_trim.py with this issuer's heading."
    )


# --- Patch 35: min-length + TOC-only safeguards ---------------------------


def test_short_slice_returns_none() -> None:
    """A 100-char slice between 1.4 and 1.5 (no real KPI body) must be
    rejected so the caller falls back to the full document text.
    """
    text = (
        "1.4 Основные финансовые показатели\n"
        + "x" * 100  # too short to be a real section
        + "\n1.5 Дальше"
    )
    out = extract_section_1_4(text, max_chars=10_000, min_section_chars=500)
    assert out.content is None
    assert "section_1_4_too_short" in out.warnings


def test_two_close_matches_flagged_as_toc_only() -> None:
    """Two anchors 200 chars apart → both treated as TOC mentions."""
    text = (
        "Содержание\n"
        "1.4 Основные финансовые показатели ............ 10\n"
        "1.4 Основные финансовые показатели ............ 11\n"
    )
    out = extract_section_1_4(
        text,
        max_chars=10_000,
        min_section_chars=0,
        toc_distance_chars=3000,
    )
    assert out.content is None
    assert "section_1_4_only_in_toc" in out.warnings


def test_two_distant_matches_uses_last() -> None:
    """Two matches separated by > toc_distance_chars → use last (real heading)."""
    body = "x" * 5000
    text = (
        "1.4 Основные финансовые показатели\n"  # TOC stub
        + body
        + "\n1.4 Основные финансовые показатели\n"  # real heading
        + "1.4.1 Прибыль 100\n1.5 Дальше"
    )
    out = extract_section_1_4(
        text,
        max_chars=10_000,
        min_section_chars=0,
        toc_distance_chars=3000,
    )
    assert out.content is not None
    assert "1.4.1" in out.content


def test_min_section_chars_threshold_boundary() -> None:
    """Section exactly at threshold passes; one char shorter rejected."""
    body_at = "x" * 600
    body_below = "x" * 100
    base = "1.4 Основные финансовые показатели\n{}\n1.5 Дальше"
    out_ok = extract_section_1_4(
        base.format(body_at), max_chars=10_000, min_section_chars=500
    )
    assert out_ok.content is not None
    out_short = extract_section_1_4(
        base.format(body_below), max_chars=10_000, min_section_chars=500
    )
    assert out_short.content is None
    assert "section_1_4_too_short" in out_short.warnings


def test_long_section_below_max_chars_returns_content() -> None:
    """Legit 5k-char section between 1.4 and 1.5 — returned unchanged."""
    body = "Чистый процентный доход 1309 млрд. " * 200  # ~7k chars
    text = (
        "1.4 Основные финансовые показатели\n" + body + "\n1.5 Сведения"
    )
    out = extract_section_1_4(text, max_chars=20_000)  # default min=500
    assert out.content is not None
    assert "Чистый процентный доход" in out.content
