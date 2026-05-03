"""Canary snapshots for self-evolve anti-regression checks (Patch 41).

Three real MOEX tickers — ``SBER``, ``LKOH``, ``IZNM`` — have stable
known-good extractions in production. Before enabling the agent on a
fresh tick we re-snapshot them and compare against a frozen baseline.
A drop in any of (publications_total / metrics_rows / written count)
indicates that some recent patch broke the main pipeline, regardless
of what the current evolve batch did.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from edx.evolve.snapshot import TickerSnapshot, snapshot_batch

CANARY_TICKERS: Final[tuple[str, ...]] = ("SBER", "LKOH", "IZNM")


@dataclass(frozen=True, slots=True)
class CanaryReport:
    ticker: str
    ok: bool
    notes: tuple[str, ...]


def canary_baseline_path(state_db: Path) -> Path:
    return state_db.parent / "canary_baseline.json"


def take_canary_baseline(
    conn: sqlite3.Connection, target_path: Path
) -> dict[str, TickerSnapshot]:
    """Persist the current canary snapshots to ``target_path``.

    Returns the snapshot dict it just wrote, mostly so that callers
    (CLI ``edx evolve canary capture``) can print a confirmation.
    """
    snaps = snapshot_batch(conn, list(CANARY_TICKERS))
    payload = {
        ticker: snap.as_json_dict() for ticker, snap in snaps.items()
    }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return snaps


def load_canary_baseline(target_path: Path) -> dict[str, TickerSnapshot]:
    """Load the on-disk baseline. Empty dict if file is missing."""
    if not target_path.exists():
        return {}
    raw = json.loads(target_path.read_text(encoding="utf-8"))
    out: dict[str, TickerSnapshot] = {}
    for ticker, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        out[ticker] = TickerSnapshot(
            ticker=payload.get("ticker", ticker),
            publications_total=int(payload.get("publications_total", 0)),
            publications_by_status=dict(
                payload.get("publications_by_status", {})
            ),
            documents_total=int(payload.get("documents_total", 0)),
            metrics_rows=int(payload.get("metrics_rows", 0)),
            metrics_by_standard=dict(payload.get("metrics_by_standard", {})),
            qa_issues_count=int(payload.get("qa_issues_count", 0)),
            qa_issues_codes=dict(payload.get("qa_issues_codes", {})),
            last_publication_date=payload.get("last_publication_date"),
        )
    return out


def check_canaries(
    conn: sqlite3.Connection,
    baseline_path: Path,
) -> list[CanaryReport]:
    """For each canary, compare current to baseline.

    A canary passes when: publications_total >= baseline,
    metrics_rows >= baseline, and publications_by_status['written'] >=
    baseline. Otherwise the report carries diagnostic notes.
    Missing baseline → all canaries report ``ok=True`` with a note
    ``baseline_missing`` (so the loop doesn't hard-fail before the
    operator captures one).
    """
    baseline = load_canary_baseline(baseline_path)
    if not baseline:
        return [
            CanaryReport(
                ticker=t, ok=True, notes=("baseline_missing",)
            )
            for t in CANARY_TICKERS
        ]

    current = snapshot_batch(conn, list(CANARY_TICKERS))
    reports: list[CanaryReport] = []
    for ticker in CANARY_TICKERS:
        before = baseline.get(ticker)
        after = current[ticker]
        if before is None:
            reports.append(
                CanaryReport(
                    ticker=ticker,
                    ok=True,
                    notes=(f"{ticker}_missing_in_baseline",),
                )
            )
            continue

        notes: list[str] = []
        ok = True
        if after.publications_total < before.publications_total:
            ok = False
            notes.append(
                f"publications {before.publications_total}→"
                f"{after.publications_total}"
            )
        if after.metrics_rows < before.metrics_rows:
            ok = False
            notes.append(
                f"metrics {before.metrics_rows}→{after.metrics_rows}"
            )
        before_written = before.publications_by_status.get("written", 0)
        after_written = after.publications_by_status.get("written", 0)
        if after_written < before_written:
            ok = False
            notes.append(
                f"publications.written {before_written}→{after_written}"
            )

        reports.append(
            CanaryReport(ticker=ticker, ok=ok, notes=tuple(notes))
        )

    return reports


__all__ = [
    "CANARY_TICKERS",
    "CanaryReport",
    "canary_baseline_path",
    "check_canaries",
    "load_canary_baseline",
    "take_canary_baseline",
]
