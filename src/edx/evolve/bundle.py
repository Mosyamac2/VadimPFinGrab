"""Diagnostic Bundle assembly for one self-evolve tick (Patch 41).

The bundle is a single directory ``evolution/runs/{tick_id}/`` packed
with everything the agent needs to diagnose a failing batch:

  - ``batch.json``           — three companies + per-ticker verdict
  - ``snap_before.json``     — already produced by tick.py
  - ``snap_after.json``      — already produced by tick.py
  - ``pipeline.log``         — full structured log of the subprocess
  - ``pipeline.log.errors``  — pre-filtered error/warning subset
  - ``state-slice.json``     — per-ticker query of state.sqlite
  - ``state-slice.txt``      — human-readable rendering
  - ``failure_taxonomy.json``— per-ticker classification + hint
  - ``canary_check.json``    — current canary report
  - ``memory_snapshot.md``   — frozen copy of evolution/MEMORY.md
  - ``recent_commits.txt``   — last 20 git commits for context
  - ``prompt.md``            — system prompt (Patch 41 stub; Patch 42
                                materialises the full content)

All sizes are capped (pipeline.log → tail of last 50_000 lines if huge,
errors filter ≤ 500KB, evidence ≤ 5 lines per ticker). Secrets are
never copied — by construction, neither pipeline.log nor state contain
``.env`` data.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from edx.evolve.canaries import (
    CanaryReport,
    canary_baseline_path,
    check_canaries,
)
from edx.evolve.csv_loader import CompanyRow
from edx.evolve.snapshot import TickerSnapshot
from edx.evolve.taxonomy import TaxonomyEntry, classify_failures
from edx.evolve.verdict import TickerVerdict

MAX_LOG_LINES: Final[int] = 50_000
MAX_ERRORS_BYTES: Final[int] = 500 * 1024


def assemble(
    bundle_dir: Path,
    *,
    batch: list[CompanyRow],
    snaps_before: dict[str, TickerSnapshot],
    snaps_after: dict[str, TickerSnapshot],
    verdicts: dict[str, TickerVerdict],
    log_path: Path,
    state_db: Path,
    conn: sqlite3.Connection,
    memory_md_path: Path = Path("evolution/MEMORY.md"),
) -> dict[str, Any]:
    """Build the Diagnostic Bundle.

    Returns a manifest dict (the ``manifest.json`` content) for logs.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)

    failing_tickers = [
        t for t, v in verdicts.items()
        if v.code in ("fail", "regression")
    ]

    # 1. batch.json — overwrite Patch 40 minimal version with verdict-aware payload.
    batch_payload = [
        {
            "company_id": c.company_id,
            "name": c.name,
            "ticker": c.synthetic_ticker,
            "profile": c.type,
            "verdict": verdicts[c.synthetic_ticker].code,
            "metrics_delta": verdicts[c.synthetic_ticker].metrics_delta,
            "notes": list(verdicts[c.synthetic_ticker].notes),
        }
        for c in batch
    ]
    (bundle_dir / "batch.json").write_text(
        json.dumps(batch_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 2. pipeline.log.errors — filtered tail.
    _filter_errors(log_path, bundle_dir / "pipeline.log.errors")

    # 3. pipeline.log might be giant — keep only the last MAX_LOG_LINES.
    _cap_log_tail(log_path, MAX_LOG_LINES)

    # 4. state-slice (per-ticker query).
    tickers = [c.synthetic_ticker for c in batch]
    state_slice = _build_state_slice(conn, tickers)
    (bundle_dir / "state-slice.json").write_text(
        json.dumps(state_slice, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _render_state_slice_text(state_slice, bundle_dir / "state-slice.txt")

    # 5. taxonomy.
    pipeline_rc = _extract_pipeline_returncode(verdicts)
    taxonomy: list[TaxonomyEntry] = (
        classify_failures(log_path, state_slice, failing_tickers, pipeline_rc)
        if failing_tickers
        else []
    )
    (bundle_dir / "failure_taxonomy.json").write_text(
        json.dumps(
            [asdict(entry) for entry in taxonomy],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 6. canary report.
    canary_reports: list[CanaryReport] = check_canaries(
        conn, canary_baseline_path(state_db)
    )
    (bundle_dir / "canary_check.json").write_text(
        json.dumps(
            [asdict(c) for c in canary_reports],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 7. memory snapshot — frozen copy.
    memory_dst = bundle_dir / "memory_snapshot.md"
    if memory_md_path.exists():
        shutil.copyfile(memory_md_path, memory_dst)
    else:
        memory_dst.write_text("", encoding="utf-8")

    # 8. recent commits (best-effort).
    _write_recent_commits(bundle_dir / "recent_commits.txt")

    # 9. prompt.md — Patch 41 stub.
    _write_prompt_stub(
        bundle_dir / "prompt.md",
        bundle_dir=bundle_dir,
        failing_tickers=failing_tickers,
        taxonomy=taxonomy,
    )

    manifest = {
        "tick_dir": str(bundle_dir),
        "files": sorted(p.name for p in bundle_dir.iterdir() if p.is_file()),
        "failing_tickers": failing_tickers,
        "taxonomy_codes": [entry.code for entry in taxonomy],
        "canary_ok": all(c.ok for c in canary_reports),
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _extract_pipeline_returncode(verdicts: dict[str, TickerVerdict]) -> int:
    """Extract pipeline subprocess returncode from verdict notes.

    ``compute_verdict`` stores ``f"returncode={rc}"`` in notes when rc != 0.
    All tickers in a batch share the same subprocess, so we return the first
    non-zero returncode found, or 0 when all ran cleanly / notes are absent.
    """
    for v in verdicts.values():
        for note in v.notes:
            if note.startswith("returncode="):
                try:
                    return int(note.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
    return 0


def _filter_errors(log_path: Path, target: Path) -> None:
    """Copy only ``"level": "error"`` / ``"warning"`` lines."""
    if not log_path.exists():
        target.write_text("", encoding="utf-8")
        return

    written = 0
    with (
        log_path.open(encoding="utf-8", errors="replace") as src,
        target.open("w", encoding="utf-8") as dst,
    ):
        for line in src:
            if (
                '"level": "error"' in line
                or '"level":"error"' in line
                or '"level": "warning"' in line
                or '"level":"warning"' in line
            ):
                if written + len(line) > MAX_ERRORS_BYTES:
                    dst.write("[truncated]\n")
                    return
                dst.write(line)
                written += len(line)


def _cap_log_tail(log_path: Path, max_lines: int) -> None:
    """If ``log_path`` is longer than ``max_lines`` keep only the tail."""
    if not log_path.exists():
        return
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    if len(lines) <= max_lines:
        return
    tail = lines[-max_lines:]
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write(
            f"[truncated {len(lines) - max_lines} earlier lines]\n"
        )
        fh.writelines(tail)


def _build_state_slice(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, dict[str, object]]:
    """Per-ticker dict of state.sqlite rows (publications/docs/metrics/qa)."""
    slice_: dict[str, dict[str, object]] = {}
    for ticker in tickers:
        publications = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM publications WHERE ticker = ? "
                "ORDER BY publication_date DESC, publication_id ASC",
                (ticker,),
            )
        ]
        documents = [
            dict(row)
            for row in conn.execute(
                """
                SELECT d.*
                  FROM documents d
                  JOIN publications p ON p.publication_id = d.publication_id
                 WHERE p.ticker = ?
                 ORDER BY d.document_id ASC
                """,
                (ticker,),
            )
        ]
        metrics = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM metrics WHERE ticker = ? "
                "ORDER BY reporting_date DESC, metric_id ASC",
                (ticker,),
            )
        ]
        qa_issues = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM qa_issues WHERE ticker = ? "
                "ORDER BY issue_id ASC",
                (ticker,),
            )
        ]
        slice_[ticker] = {
            "publications": publications,
            "documents": documents,
            "metrics": metrics,
            "qa_issues": qa_issues,
            "metrics_count": len(metrics),
        }
    return slice_


def _render_state_slice_text(
    state_slice: dict[str, dict[str, object]], target: Path
) -> None:
    lines: list[str] = []
    for ticker, sub in state_slice.items():
        lines.append(f"=== {ticker} ===")
        for key in ("publications", "documents", "metrics", "qa_issues"):
            rows = sub.get(key, [])
            n = len(rows) if isinstance(rows, list) else 0
            lines.append(f"-- {key} ({n}) --")
            if isinstance(rows, list):
                for row in rows[:10]:
                    lines.append(json.dumps(row, ensure_ascii=False))
                if n > 10:
                    lines.append(f"... [{n - 10} more]")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")


def _write_recent_commits(target: Path) -> None:
    try:
        out = subprocess.check_output(
            ["git", "log", "--oneline", "-20"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        out = "git unavailable\n"
    target.write_text(out, encoding="utf-8")


def _write_prompt_stub(
    target: Path,
    *,
    bundle_dir: Path,
    failing_tickers: list[str],
    taxonomy: list[TaxonomyEntry],
) -> None:
    """Patch 41 stub — Patch 42 will produce a full slash-command prompt."""
    classes_set: set[str] = {entry.code for entry in taxonomy}
    classes_str = ", ".join(sorted(classes_set)) if classes_set else "(none)"
    payload = (
        f"# Tick bundle: {bundle_dir}\n"
        f"\n"
        f"This is a Patch 41 stub. Patch 42 wires Claude Code to consume it.\n"
        f"\n"
        f"Failing tickers: {failing_tickers or '(none)'}\n"
        f"Failure classes: {classes_str}\n"
    )
    target.write_text(payload, encoding="utf-8")


__all__ = ["assemble"]
