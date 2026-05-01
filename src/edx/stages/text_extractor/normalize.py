"""Pure-function text cleanup applied after native or OCR extraction."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

SOFT_HYPHEN = "­"

_HYPHEN_LINEBREAK = re.compile(r"(\w)[-‐-—]\n(\w)", re.UNICODE)
_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_RUNS_OF_SPACE = re.compile(r"[ \t]{2,}")


def _basic_clean(text: str) -> str:
    if not text:
        return ""
    # Normalise unicode (NBSP → space, fix decomposed characters).
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(SOFT_HYPHEN, "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Reattach hyphenated words split across lines: "feder-\nальной" → "федеральной".
    text = _HYPHEN_LINEBREAK.sub(r"\1\2", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _RUNS_OF_SPACE.sub(" ", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


def normalize_text(
    pages: Iterable[str],
    *,
    header_footer_min_pages: int = 3,
) -> list[str]:
    """Clean each page; on multi-page docs, strip recurring header/footer lines.

    A line is treated as a header/footer if it appears identically on at least
    ``header_footer_min_pages`` distinct pages **and** that count is greater
    than half of the document length (so 1-page docs never get stripped).
    """
    cleaned = [_basic_clean(p) for p in pages]
    if len(cleaned) < header_footer_min_pages:
        return cleaned

    # Count short, non-empty lines (header/footer candidates are usually
    # ≤ 80 chars). Skip very long lines to avoid stripping body paragraphs
    # that happen to repeat.
    counter: Counter[str] = Counter()
    for page in cleaned:
        seen_on_page: set[str] = set()
        for line in page.splitlines():
            stripped = line.strip()
            if not stripped or len(stripped) > 120:
                continue
            if stripped in seen_on_page:
                continue
            seen_on_page.add(stripped)
            counter[stripped] += 1

    threshold = max(header_footer_min_pages, len(cleaned) // 2 + 1)
    recurring = {line for line, count in counter.items() if count >= threshold}
    if not recurring:
        return cleaned

    out: list[str] = []
    for page in cleaned:
        kept_lines = [
            line for line in page.splitlines() if line.strip() not in recurring
        ]
        out.append("\n".join(kept_lines).strip())
    return out
