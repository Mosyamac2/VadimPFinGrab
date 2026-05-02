"""Trim a Russian RSBU report PDF down to the balance-sheet onwards.

Patch 30: when a РСБУ document opens with a multi-page audit opinion
(Кэпт / Б1 / ДРТ / Делойт preamble: 5–30k characters of methodology,
key audit matters, ICFR commentary), feeding it whole into the LLM
both wastes tokens and risks hallucination — phrases like
«прибыль 100 млн» can appear in audit reasoning context unrelated
to the actual reported numbers.

This module locates the start of the actual financial-form block by
matching one of the well-known anchors, then returns the slice from
that anchor onwards (capped to ``max_chars``). A short pre-header is
prepended so the LLM is reminded that what follows are RSBU forms,
not free narrative.

Anchors recognised, in priority order by **earliest occurrence in
text**:

- ``БУХГАЛТЕРСКИЙ БАЛАНС``                  (uppercase form heading)
- ``Бухгалтерский баланс``                  (capitalised variant)
- ``Форма по ОКУД 0710001``                 (Minfin RSBU balance code)
- ``ОТЧЕТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ``        (P&L, fallback when
                                             balance heading is missing
                                             or appears later)

When no anchor matches, returns ``content=None`` and the caller falls
back to the full text — graceful degradation, never empty output.

Pure-function module: no I/O, no logging side effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Header prepended to the trimmed slice so the LLM knows to ignore any
# audit-style narrative that may still appear within the form section.
_LEAD_HEADER: str = (
    "Перед тобой формы РСБУ-отчётности (баланс, ОФР, отчёт об "
    "изменениях капитала). Извлекай числа только из этих форм. "
    "Аудиторские пояснения, если они встретятся ниже, для KPI не "
    "используй.\n\n"
)


@dataclass(frozen=True)
class BalanceTrimResult:
    """Result of :func:`extract_balance_sheet_onwards`.

    ``content`` is ``None`` when no anchor matched — the caller should
    fall back to the full text.
    """

    content: str | None
    anchor_label_seen: str | None
    warnings: tuple[str, ...]


# --- regex anchors -------------------------------------------------------

# Balance-sheet heading on its own line. Case-insensitive — Russian
# issuers use both ALL-CAPS («БУХГАЛТЕРСКИЙ БАЛАНС») and capitalised
# («Бухгалтерский баланс») depending on the typesetter.
_ANCHOR_BALANCE_HEADING = re.compile(
    r"(?im)^\s*Бухгалтерский\s+баланс\s*$"
)
# OKUD 0710001 = Minfin code for the RSBU balance-sheet form. Tolerate
# leading zero, whitespace including newlines between tokens.
_ANCHOR_OKUD_BALANCE = re.compile(
    r"(?i)\bФорма\s+по\s+ОКУД\s+0?710001\b"
)
# Profit & loss heading — fallback when the balance heading is absent
# or appears later in the file (some issuers emit P&L first). Tolerate
# both ё and е spellings of «Отчёт».
_ANCHOR_PL_HEADING = re.compile(
    r"^\s*Отч[её]т\s+о\s+финансовых\s+результатах\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _normalise_label(text: str) -> str:
    """Collapse runs of whitespace; keep original casing."""
    return " ".join(text.split())


def extract_balance_sheet_onwards(
    text: str,
    *,
    max_chars: int = 200_000,
) -> BalanceTrimResult:
    """Cut ``text`` to the slice that starts at the first balance anchor.

    Returns ``BalanceTrimResult(content=None, ...)`` when no anchor
    matches; the Metric Extractor then falls back to the full text.
    Otherwise the returned content is ``_LEAD_HEADER`` + the slice from
    the earliest matching anchor (capped to ``max_chars``).
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    if not text:
        return BalanceTrimResult(
            content=None,
            anchor_label_seen=None,
            warnings=("balance_anchor_not_found",),
        )

    # Probe all anchor regexes in parallel. The earliest match in the
    # text wins — it is invariably the start of the form section
    # (audit preamble never contains "БУХГАЛТЕРСКИЙ БАЛАНС" centered
    # on its own line, and OKUD codes only appear inside the forms).
    # Heading regexes echo the original casing from the text; the OKUD
    # anchor uses a fixed canonical label for stable log lines.
    candidates: list[tuple[int, str]] = []

    balance_match = _ANCHOR_BALANCE_HEADING.search(text)
    if balance_match is not None:
        candidates.append(
            (balance_match.start(), _normalise_label(balance_match.group(0)))
        )

    okud_match = _ANCHOR_OKUD_BALANCE.search(text)
    if okud_match is not None:
        candidates.append((okud_match.start(), "ОКУД 0710001"))

    pl_match = _ANCHOR_PL_HEADING.search(text)
    if pl_match is not None:
        candidates.append(
            (pl_match.start(), _normalise_label(pl_match.group(0)))
        )

    if not candidates:
        return BalanceTrimResult(
            content=None,
            anchor_label_seen=None,
            warnings=("balance_anchor_not_found",),
        )

    candidates.sort(key=lambda c: c[0])
    start_pos, anchor_label = candidates[0]
    body = text[start_pos:]
    warnings: list[str] = []
    if len(body) > max_chars:
        body = body[:max_chars]
        warnings.append("balance_trim_capped")

    return BalanceTrimResult(
        content=_LEAD_HEADER + body,
        anchor_label_seen=anchor_label,
        warnings=tuple(warnings),
    )
