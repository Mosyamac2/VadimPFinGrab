"""Housekeeping for ``evolution/runs/`` (Patch 46).

Bundle directories accumulate at ~12 ticks/hour in production; without
purging the disk fills up in days. This module provides ``purge_old_runs``
which removes directories whose corresponding tick row in
``evolution_ticks`` finished before the cutoff.

The default minimum age is 30 days. Operators can pin failed-tick
bundles for forensic work via ``keep_failed=True``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from edx.storage import EvolutionRepo


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: int
    kept: int
    removed_bytes: int


def purge_old_runs(
    runs_dir: Path,
    *,
    repo: EvolutionRepo | None,
    older_than: timedelta = timedelta(days=30),
    keep_failed: bool = False,
    now: datetime | None = None,
) -> CleanupResult:
    """Remove tick bundles older than ``older_than``.

    A bundle is eligible iff:
      - its directory name parses as ``int`` (a tick_id), AND
      - the corresponding ``evolution_ticks`` row exists with
        ``finished_at`` older than the cutoff, AND
      - either ``keep_failed`` is False OR the verdict is ``ok``.

    Bundles without a matching row are LEFT IN PLACE — they may be
    pre-DB ad-hoc artefacts the operator put there manually.
    """
    if not runs_dir.exists():
        return CleanupResult(removed=0, kept=0, removed_bytes=0)

    cutoff = (now or datetime.now(UTC)) - older_than
    cutoff_iso = cutoff.isoformat()

    removed = 0
    kept = 0
    removed_bytes = 0

    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            tick_id = int(entry.name)
        except ValueError:
            kept += 1
            continue

        if repo is None:
            kept += 1
            continue

        tick = repo.get_tick(tick_id)
        if tick is None or tick.finished_at is None:
            kept += 1
            continue
        if tick.finished_at >= cutoff_iso:
            kept += 1
            continue
        if keep_failed and tick.verdict != "ok":
            kept += 1
            continue

        size = _dir_size(entry)
        shutil.rmtree(entry)
        removed += 1
        removed_bytes += size

    return CleanupResult(
        removed=removed, kept=kept, removed_bytes=removed_bytes
    )


def _dir_size(path: Path) -> int:
    total = 0
    for sub in path.rglob("*"):
        try:
            total += sub.stat().st_size
        except FileNotFoundError:
            continue
    return total


def parse_duration(value: str) -> timedelta:
    """Tiny duration parser: ``30d`` / ``12h`` / ``45m`` / ``1w``."""
    import re

    match = re.match(r"^\s*(\d+)\s*([smhdw])\s*$", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid duration {value!r}")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    return timedelta(weeks=amount)


__all__ = ["CleanupResult", "parse_duration", "purge_old_runs"]
