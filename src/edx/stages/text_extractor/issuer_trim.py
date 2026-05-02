"""Trim an Issuer Report PDF down to its section 1.4 (KPI block).

Quarterly Issuer Reports (Положение Банка России 454-П, type=5 on
e-disclosure.ru) are 50–100 pages of MD&A, governance, and risk
disclosures, with the only LLM-friendly KPI table living in section
**1.4 «Основные финансовые показатели»** (3–6 pages). Sending the whole
report would burn tens of thousands of tokens for an extraction the
LLM does best on the trimmed slice.

Patch 21 ships a regex-based extractor that locates the section by its
heading and returns just the slice between section 1.4 and the next
section start (1.5 or 2). Three label spellings are supported because
real issuers use them inconsistently:

- "Основные финансовые показатели"                       (most common)
- "Основные финансово-экономические показатели"          (older 454-P wording)
- "Основные показатели финансово-хозяйственной деятельности" (small issuers)

Whitespace flexibility:
- ``1.4`` may be ``1 . 4`` or ``1.4.`` (extra punctuation)
- NBSP / thin space between ``1.4`` and the label, and between
  multi-word labels.
- Hyphen in ``финансово-экономические`` may be a regular ``-``, an
  en-dash ``–`` or em-dash ``—``.

When no anchor matches, the function returns ``content=None`` with a
warning — the Metric Extractor falls back to the full text in that
case (graceful degradation rather than empty output).

Pure-function module: no I/O, no logging side effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SectionExtract:
    """Result of :func:`extract_section_1_4`.

    ``content`` is ``None`` when the start anchor wasn't found — the
    caller should fall back to the full text.
    """

    content: str | None
    anchor_label_seen: str | None
    end_anchor_seen: str | None
    warnings: list[str]


# --- regex anchors --------------------------------------------------------

# ``1.4`` (or ``1 . 4`` / ``1.4.``) followed by one of the three label
# variants. Compiled with ``re.UNICODE`` (default in Python 3) and
# ``re.IGNORECASE`` so ``Основные`` / ``ОСНОВНЫЕ`` both match.
ANCHOR_START = re.compile(
    r"""
    1\s*\.\s*4\s*\.?\s*               # 1.4 with optional trailing dot
    (?P<label>
        Основные\s+финансовые\s+показатели
        |
        Основные\s+финансово[\s\-‐-―]*
        экономические\s+показатели
        |
        Основные\s+показатели\s+финансово[\s\-‐-―]*
        хозяйственной\s+деятельности
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# End anchor: the next section header. Either ``1.5 …`` or ``2. <Word>``.
# The trailing ``\S`` on the ``2.`` form prevents matching a ``2.`` that
# happens to sit in a numeric value (e.g. "ROE = 12.5%") without text
# right after — section headers always have a label.
ANCHOR_END_SECTION_15 = re.compile(
    r"^\s*1\s*\.\s*5\s*[.\s]", re.MULTILINE
)
ANCHOR_END_SECTION_2 = re.compile(
    r"^\s*2\s*\.\s+\S", re.MULTILINE
)


def extract_section_1_4(text: str, *, max_chars: int) -> SectionExtract:
    """Cut ``text`` down to the slice that holds section 1.4.

    Returns the original text (clipped to ``max_chars``) when the start
    anchor isn't found, with a warning. Callers can then decide whether
    to send the trimmed slice or fall back to the full document.
    """
    warnings: list[str] = []
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    if not text:
        return SectionExtract(
            content=None,
            anchor_label_seen=None,
            end_anchor_seen=None,
            warnings=["empty input text"],
        )

    # Issuer Reports start with a table-of-contents that lists section
    # 1.4 as a single line ("1.4   Основные финансовые показатели ... 10");
    # the real section header appears further down. Take the LAST match —
    # for one-shot documents (no TOC) there's only one and last == first.
    matches = list(ANCHOR_START.finditer(text))
    if not matches:
        warnings.append(
            "section 1.4 start anchor not found — caller should fall back "
            "to the full document text"
        )
        return SectionExtract(
            content=None,
            anchor_label_seen=None,
            end_anchor_seen=None,
            warnings=warnings,
        )
    start_match = matches[-1]
    if len(matches) > 1:
        warnings.append(
            f"section 1.4 anchor matched {len(matches)} times "
            "(likely TOC + real heading) — using the last match"
        )

    start_pos = start_match.start()
    label_seen = " ".join(start_match.group("label").split())

    # Search for end anchors strictly *after* the start position.
    end_pos = len(text)
    end_anchor_seen: str | None = None
    end_15 = ANCHOR_END_SECTION_15.search(text, pos=start_match.end())
    end_2 = ANCHOR_END_SECTION_2.search(text, pos=start_match.end())
    candidates: list[tuple[int, str]] = []
    if end_15 is not None:
        candidates.append((end_15.start(), "1.5"))
    if end_2 is not None:
        candidates.append((end_2.start(), "2."))
    if candidates:
        candidates.sort()
        end_pos, end_anchor_seen = candidates[0]
    else:
        warnings.append(
            "section 1.4 end anchor not found — content truncated by max_chars"
        )

    content = text[start_pos:end_pos]
    if len(content) > max_chars:
        content = content[:max_chars]
        warnings.append(
            f"section 1.4 content exceeded max_chars={max_chars} and was truncated"
        )

    return SectionExtract(
        content=content,
        anchor_label_seen=label_seen,
        end_anchor_seen=end_anchor_seen,
        warnings=warnings,
    )
