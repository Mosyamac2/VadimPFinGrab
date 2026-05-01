"""Pure HTML parsers for e-disclosure issuer cards.

The real e-disclosure.ru HTML structure may differ from the synthetic fixtures
shipped with the test suite. The parser is built around explicit CSS selectors
so the operator can adapt them to the live site without touching service code.

Selectors (defaults):

- ``section.publications-section`` with ``data-section`` attribute set to
  ``reports`` or ``events``.
- Inside each section, ``.publication-row`` elements containing
  ``.publication-date`` (text node, dd.mm.yyyy or YYYY-MM-DD) and an
  ``a.publication-link`` with the ``href`` to the document.

Pure functions only — no I/O, no logging side effects beyond returning a list
of ``DiscoveredPublication``. The ``warnings`` argument captures soft issues
(missing dates, malformed rows) that the service layer surfaces via structlog.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

PublicationType = Literal["report", "event"]

_DATE_FORMATS = (
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
)


@dataclass(frozen=True)
class DiscoveredPublication:
    publication_id: str
    publication_type: PublicationType
    publication_date: str  # ISO-8601 (YYYY-MM-DD)
    source_url: str
    title: str


@dataclass
class ParseResult:
    """Result of parsing one issuer card: publications + soft warnings."""

    publications: list[DiscoveredPublication] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_issuer_card(
    html: str,
    *,
    base_url: str,
    ticker: str,
) -> ParseResult:
    """Extract publications from an issuer card HTML.

    Soft errors (missing date, missing href) produce warnings; the publication
    is dropped from the result but the rest of the page keeps parsing.
    """
    result = ParseResult()
    if not html.strip():
        result.warnings.append("empty html")
        return result

    tree = HTMLParser(html)

    sections = tree.css("section.publications-section")
    if not sections:
        result.warnings.append("no publications-section found")
        return result

    for section in sections:
        section_kind = (section.attributes.get("data-section") or "").strip().lower()
        if section_kind == "reports":
            pub_type: PublicationType = "report"
        elif section_kind == "events":
            pub_type = "event"
        else:
            result.warnings.append(
                f"unknown section data-section={section_kind!r}"
            )
            continue

        rows = section.css(".publication-row")
        for row in rows:
            link_node = row.css_first("a.publication-link")
            date_node = row.css_first(".publication-date")

            if link_node is None:
                result.warnings.append("publication row without link")
                continue
            href = (link_node.attributes.get("href") or "").strip()
            if not href:
                result.warnings.append(
                    f"publication row with empty href in section={pub_type!r}"
                )
                continue

            absolute_url = urljoin(base_url + "/", href)
            title = (link_node.text() or "").strip()

            if date_node is None:
                result.warnings.append(
                    f"publication row without date for url={absolute_url!r}"
                )
                continue
            iso_date = _parse_date((date_node.text() or "").strip())
            if iso_date is None:
                result.warnings.append(
                    f"unparseable date for url={absolute_url!r}: "
                    f"{date_node.text()!r}"
                )
                continue

            result.publications.append(
                DiscoveredPublication(
                    publication_id=make_publication_id(
                        absolute_url, iso_date, ticker
                    ),
                    publication_type=pub_type,
                    publication_date=iso_date,
                    source_url=absolute_url,
                    title=title,
                )
            )

    return result


def make_publication_id(url: str, date_iso: str, ticker: str) -> str:
    """Stable identifier derived from URL + date + ticker.

    Format: ``{ticker}-{YYYY-MM-DD}-{first 16 hex chars of sha256}``. Stable
    across runs as long as the URL and date don't change.
    """
    digest = hashlib.sha256(
        f"{url}|{date_iso}|{ticker}".encode()
    ).hexdigest()[:16]
    return f"{ticker}-{date_iso}-{digest}"


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
