"""Strip HTML to its main text content for Event Extractor input."""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

_NOISY_SELECTORS: tuple[str, ...] = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "noscript",
    "form",
    "iframe",
)
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """Convert raw HTML to plain text, dropping common navigation chrome.

    Pure function: no I/O, no logging. Empty input returns ``""``.
    """
    if not html.strip():
        return ""

    tree = HTMLParser(html)
    for selector in _NOISY_SELECTORS:
        for node in tree.css(selector):
            node.decompose()

    target = tree.css_first("main") or tree.css_first("article") or tree.css_first("body")
    if target is None:
        target = tree.root
    if target is None:
        return ""

    raw_text = target.text(separator="\n", strip=True)
    lines = [line.strip() for line in raw_text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return _BLANK_LINES_RE.sub("\n\n", cleaned).strip()
