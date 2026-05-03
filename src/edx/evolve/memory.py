"""Long-term memory file for the self-evolve loop (Patch 42).

The agent must read ``evolution/MEMORY.md`` in STEP 0 of every tick
and append a new ``### evolve(N) — DATE — failure_class`` entry in
STEP 4. Patch 43's verdict gate verifies that this entry exists
before merging the patch into master.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

MEMORY_PATH: Final[Path] = Path("evolution/MEMORY.md")

PATCH_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^###\s+evolve\((?P<tick>\d+)\)\s+—\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+—\s+"
    r"(?P<failure_class>[A-Za-z0-9_]+)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class MemoryDigest:
    """Lightweight summary of a parsed MEMORY.md."""

    raw: str
    patch_entries: int
    last_tick: int | None
    failure_classes: frozenset[str]
    anti_patterns_count: int


def read(path: Path = MEMORY_PATH) -> MemoryDigest:
    """Parse MEMORY.md. Missing file → empty digest."""
    if not path.exists():
        return MemoryDigest(
            raw="",
            patch_entries=0,
            last_tick=None,
            failure_classes=frozenset(),
            anti_patterns_count=0,
        )
    raw = path.read_text(encoding="utf-8")
    return _parse(raw)


def _parse(raw: str) -> MemoryDigest:
    matches = list(PATCH_HEADER_RE.finditer(raw))
    classes = frozenset(m.group("failure_class") for m in matches)
    last_tick = max(int(m.group("tick")) for m in matches) if matches else None
    anti_patterns_count = _count_anti_patterns(raw)
    return MemoryDigest(
        raw=raw,
        patch_entries=len(matches),
        last_tick=last_tick,
        failure_classes=classes,
        anti_patterns_count=anti_patterns_count,
    )


_ANTIPATTERN_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^##\s+Anti-patterns\b", re.MULTILINE
)
_NEXT_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^##[^#]", re.MULTILINE
)


def _count_anti_patterns(raw: str) -> int:
    """Count ``- ``/``* `` bullets inside the Anti-patterns section."""
    header = _ANTIPATTERN_HEADER_RE.search(raw)
    if header is None:
        return 0
    body_start = header.end()
    next_section = _NEXT_HEADER_RE.search(raw, pos=body_start)
    body = raw[body_start : next_section.start() if next_section else len(raw)]
    bullets = re.findall(r"^\s*[-*]\s+\S", body, flags=re.MULTILINE)
    return len(bullets)


def has_new_entry_since(
    before_raw: str, after_raw: str, tick_id: int
) -> bool:
    """True iff ``after_raw`` contains a ``### evolve(tick_id) — …`` header
    that ``before_raw`` does not."""
    needle = f"evolve({tick_id})"
    after_has = _has_tick_header(after_raw, tick_id)
    before_has = _has_tick_header(before_raw, tick_id)
    if not after_has:
        return False
    if before_has:
        # Already there before — agent didn't add a new one.
        return False
    # Sanity: at least one occurrence of "evolve(N)" in the after diff.
    return needle in after_raw and needle not in before_raw


def _has_tick_header(raw: str, tick_id: int) -> bool:
    pattern = re.compile(
        rf"^###\s+evolve\({tick_id}\)\s+—\s+\d{{4}}-\d{{2}}-\d{{2}}\s+—\s+",
        re.MULTILINE,
    )
    return pattern.search(raw) is not None


def diff_summary(before_raw: str, after_raw: str) -> str:
    """Cheap textual diff for logs (NOT for git)."""
    before_lines = before_raw.splitlines()
    after_lines = after_raw.splitlines()
    if before_lines == after_lines:
        return "(no change)"
    added = len(after_lines) - len(before_lines)
    return f"lines: {len(before_lines)} → {len(after_lines)} (Δ {added:+d})"


__all__ = [
    "MEMORY_PATH",
    "MemoryDigest",
    "diff_summary",
    "has_new_entry_since",
    "read",
]
