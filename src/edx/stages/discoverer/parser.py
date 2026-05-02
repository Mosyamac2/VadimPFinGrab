"""Pure HTML parser for e-disclosure file-listing pages.

Targets the real markup served at
``https://www.e-disclosure.ru/portal/files.aspx?id=X&type=Y``:

.. code-block:: html

    <table class="zebra noBorderTbl centerHeader files-table">
      <tbody>
        <tr><th>№</th><th>Тип документа</th><th>Отчётный период</th>
            <th>Дата основания</th><th>Дата размещения</th>
            <th>Файл</th><th></th></tr>
        <tr>
          <td class="row-number-cell">1</td>
          <td class="type-cell">Промежуточная МСФО ...</td>
          <td>2026, 3 месяца</td>
          <td class="date-cell">28.04.2026</td>
          <td class="date-cell">29.04.2026</td>
          <td class="file-cell">
            <a class="file-link"
               href="https://www.e-disclosure.ru/portal/FileLoad.ashx?Fileid=1924258"
               data-fileid="1924258">zip,&nbsp;2.09&nbsp;МБ</a>
          </td>
        </tr>
        ...
      </tbody>
    </table>

Some saved snapshots come from Firefox "View Source" — the real markup is
wrapped in ``<span id="lineN">`` elements and HTML-escaped. The parser detects
that wrapper and unwraps it once before walking the table.

Pure functions only — no I/O, no logging side effects beyond the returned
:class:`ParseResult`. The service layer surfaces ``warnings`` via structlog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from selectolax.parser import HTMLParser

from edx.stages.discoverer.period import parse_reporting_period
from edx.storage.models import PeriodType

PublicationType = Literal["report", "event"]

_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")

# Reporting standard derived from the ``type`` query parameter on the listing
# URL. ``type=2`` (annual disclosure) is metadata-only — it carries the issuer
# annual report (МДА/ESG/risks), not financial statements; the Metric
# Extractor doesn't read it. ``type=5`` is the issuer report which contains
# the section 1.4 KPI summary (used as third-priority source by Patch 21).
_TYPE_CODE_TO_STANDARD: dict[int, str] = {
    2: "ANNUAL",
    3: "RSBU",
    4: "IFRS",
    5: "ISSUER",
}


@dataclass(frozen=True)
class DiscoveredPublication:
    """One row from a ``files.aspx?id=X&type=Y`` listing.

    Patch 16 extends the contract with four fields derived deterministically
    from the URL and the listing table — so downstream stages don't have to
    guess the report type or period from the PDF text.
    """

    publication_id: str
    publication_type: PublicationType
    publication_date: str  # ISO-8601 (YYYY-MM-DD), "Дата размещения"
    source_url: str
    title: str
    report_type_code: int | None = None
    report_type_label: str | None = None
    reporting_period_year: int | None = None
    reporting_period_type: PeriodType | None = None


@dataclass
class ParseResult:
    publications: list[DiscoveredPublication] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def reporting_standard_for_type(type_code: int) -> str | None:
    """Map a ``type`` query parameter to a logical reporting standard."""
    return _TYPE_CODE_TO_STANDARD.get(type_code)


def parse_listing_page(
    html: str,
    *,
    base_url: str,
    ticker: str,
    type_code: int,
) -> ParseResult:
    """Extract every row of a ``files.aspx`` listing into ``DiscoveredPublication``.

    Soft errors (missing period label, missing ``data-fileid``, unparseable
    date) yield warnings and skip the offending row; the rest of the table
    keeps parsing.

    A 200-OK page with no ``table.files-table`` (issuer doesn't publish this
    type, e.g. LKOH ``id=17`` ``type=4``) returns an empty result with no
    warnings — the service treats it as "type unavailable", not as an error.
    """
    result = ParseResult()
    if not html.strip():
        result.warnings.append("empty html")
        return result

    real_html = _unwrap_view_source(html)
    tree = HTMLParser(real_html)

    table = tree.css_first("table.files-table")
    if table is None:
        # 200 OK but the issuer simply doesn't publish this type. Service
        # layer logs a single info message; no warning here.
        return result

    tbody = table.css_first("tbody")
    rows = tbody.css("tr") if tbody is not None else table.css("tr")
    for row in rows:
        # Skip header rows — they only contain ``<th>``.
        if row.css_first("th") is not None and row.css_first("td") is None:
            continue
        cells = row.css("td")
        if not cells:
            continue
        if len(cells) < 6:
            result.warnings.append(
                f"row with only {len(cells)} cells (expected ≥6): "
                f"{_short(row.text(strip=True))}"
            )
            continue

        type_label = cells[1].text(strip=True)
        period_text = cells[2].text(strip=True)
        # Layout verified on real fixtures (SBER types 2/3/4 + LKOH type 3):
        #   types 3/4:  [#, type, period, date_basis, date_post, file, cert]   (7 cells)
        #   type 2:     [#, type, period, date_extra, date_basis, date_post, file, cert]   (8 cells)
        # The trailing ``td.cert-cell`` is always present (empty), the file
        # cell is always second-to-last. ``date_post`` is therefore third
        # from the end. The cell-count check above guarantees ``cells[-3]``
        # is valid.
        publication_date_text = cells[-3].text(strip=True)

        link = row.css_first("a.file-link") or row.css_first("td.file-cell a")
        if link is None:
            result.warnings.append(
                f"row without file link: {_short(type_label)}"
            )
            continue
        href = (link.attributes.get("href") or "").strip()
        file_id = (link.attributes.get("data-fileid") or "").strip()
        if not href or not file_id:
            result.warnings.append(
                f"row with empty href/data-fileid: {_short(type_label)}"
            )
            continue

        iso_date = _parse_date(publication_date_text)
        if iso_date is None:
            result.warnings.append(
                f"unparseable publication_date {publication_date_text!r} "
                f"for fileid={file_id}"
            )
            continue

        period = parse_reporting_period(period_text, type_code=type_code)
        if period is None and period_text:
            result.warnings.append(
                f"unrecognised reporting period {period_text!r} "
                f"for fileid={file_id}"
            )

        result.publications.append(
            DiscoveredPublication(
                publication_id=f"{ticker}-{type_code}-{file_id}",
                publication_type="report",
                publication_date=iso_date,
                source_url=href,
                title=type_label,
                report_type_code=type_code,
                report_type_label=type_label or None,
                reporting_period_year=period.year if period else None,
                reporting_period_type=period.period_type if period else None,
            )
        )

    return result


def _unwrap_view_source(html: str) -> str:
    """Strip Firefox view-source ``<span id="lineN">…</span>`` wrapping.

    The wrapped page renders the *real* markup as visible text inside spans.
    Calling ``HTMLParser(...).body.text()`` returns that text — i.e. the real
    markup as a string — which we then re-parse. If the input is already a
    plain HTML page with a real ``<table>`` element, return it unchanged.
    """
    if "<table" in html:
        return html
    tree = HTMLParser(html)
    body = tree.body
    if body is None:
        return html
    inner = body.text()
    if "<table" in inner:
        return inner
    return html


def _short(text: str, limit: int = 80) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _parse_date(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None
