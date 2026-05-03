"""Diagnostic Bundle assembly (Patch 41)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from edx.evolve import bundle as bundle_module
from edx.evolve.csv_loader import CompanyRow
from edx.evolve.snapshot import TickerSnapshot
from edx.evolve.verdict import TickerVerdict
from edx.storage import Database


def _make_batch() -> list[CompanyRow]:
    return [
        CompanyRow(company_id="1", name="Co1", type="non_bank"),
        CompanyRow(company_id="2", name="Co2", type="non_bank"),
        CompanyRow(company_id="3", name="Co3", type="non_bank"),
    ]


def _empty_snap(ticker: str) -> TickerSnapshot:
    return TickerSnapshot(
        ticker=ticker,
        publications_total=0,
        publications_by_status={},
        documents_total=0,
        metrics_rows=0,
        metrics_by_standard={},
        qa_issues_count=0,
        qa_issues_codes={},
        last_publication_date=None,
    )


def _verdict(ticker: str, code: str = "fail") -> TickerVerdict:
    return TickerVerdict(
        ticker=ticker,
        code=code,  # type: ignore[arg-type]
        metrics_delta=0,
        publications_written_delta=0,
        qa_issues_delta=0,
        notes=(),
    )


def test_bundle_creates_all_files(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    bundle_dir = tmp_path / "tick_runs" / "1"
    log_path = tmp_path / "pipeline.log"
    log_path.write_text(
        json.dumps(
            {
                "event": "discoverer_non_200",
                "level": "warning",
                "ticker": "EDX1",
                "status": 403,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    batch = _make_batch()
    snaps_before = {f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)}
    snaps_after = {f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)}
    verdicts = {
        "EDX1": _verdict("EDX1", "fail"),
        "EDX2": _verdict("EDX2", "ok"),
        "EDX3": _verdict("EDX3", "fail"),
    }

    manifest = bundle_module.assemble(
        bundle_dir,
        batch=batch,
        snaps_before=snaps_before,
        snaps_after=snaps_after,
        verdicts=verdicts,
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )

    expected_files = {
        "batch.json",
        "pipeline.log.errors",
        "state-slice.json",
        "state-slice.txt",
        "failure_taxonomy.json",
        "canary_check.json",
        "memory_snapshot.md",
        "recent_commits.txt",
        "prompt.md",
        "manifest.json",
    }
    actual_files = {p.name for p in bundle_dir.iterdir() if p.is_file()}
    assert expected_files.issubset(actual_files)

    # batch.json contains verdicts
    raw_batch = json.loads((bundle_dir / "batch.json").read_text())
    assert {row["verdict"] for row in raw_batch} == {"fail", "ok"}

    # taxonomy non-empty for failing tickers
    raw_tax = json.loads(
        (bundle_dir / "failure_taxonomy.json").read_text()
    )
    assert {entry["ticker"] for entry in raw_tax} == {"EDX1", "EDX3"}

    # manifest reflects failure tickers
    assert manifest["failing_tickers"] == ["EDX1", "EDX3"]


def test_bundle_pipeline_log_errors_filter(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    bundle_dir = tmp_path / "b"
    log_path = tmp_path / "pipeline.log"
    log_path.write_text(
        '{"level": "info", "event": "noise"}\n'
        '{"level": "error", "event": "boom"}\n'
        '{"level": "warning", "event": "yellow"}\n'
        '{"level": "info", "event": "more noise"}\n',
        encoding="utf-8",
    )
    bundle_module.assemble(
        bundle_dir,
        batch=_make_batch(),
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts={f"EDX{i}": _verdict(f"EDX{i}", "fail") for i in (1, 2, 3)},
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )
    errors = (bundle_dir / "pipeline.log.errors").read_text(encoding="utf-8")
    assert "boom" in errors
    assert "yellow" in errors
    assert "noise" not in errors


def test_bundle_memory_snapshot_frozen(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    bundle_dir = tmp_path / "b"
    log_path = tmp_path / "p.log"
    log_path.write_text("", encoding="utf-8")
    memory = tmp_path / "MEMORY.md"
    memory.write_text("# initial\n", encoding="utf-8")

    bundle_module.assemble(
        bundle_dir,
        batch=_make_batch(),
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts={f"EDX{i}": _verdict(f"EDX{i}", "fail") for i in (1, 2, 3)},
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
        memory_md_path=memory,
    )

    snapshot = (bundle_dir / "memory_snapshot.md").read_text(encoding="utf-8")
    assert snapshot == "# initial\n"

    # Modify the source — snapshot must NOT change.
    memory.write_text("# initial\n# more\n", encoding="utf-8")
    assert (bundle_dir / "memory_snapshot.md").read_text(encoding="utf-8") == (
        "# initial\n"
    )


def test_bundle_handles_empty_state(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """No publications/docs/metrics/qa for any ticker — must still build bundle."""
    bundle_dir = tmp_path / "empty"
    log_path = tmp_path / "p.log"
    log_path.write_text("", encoding="utf-8")
    bundle_module.assemble(
        bundle_dir,
        batch=_make_batch(),
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts={f"EDX{i}": _verdict(f"EDX{i}", "fail") for i in (1, 2, 3)},
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )
    assert (bundle_dir / "manifest.json").exists()
    state = json.loads((bundle_dir / "state-slice.json").read_text())
    for ticker in ("EDX1", "EDX2", "EDX3"):
        assert state[ticker]["publications"] == []
        assert state[ticker]["metrics"] == []


def test_bundle_no_failing_tickers_still_writes_taxonomy_empty(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    bundle_dir = tmp_path / "ok"
    log_path = tmp_path / "p.log"
    log_path.write_text("", encoding="utf-8")
    bundle_module.assemble(
        bundle_dir,
        batch=_make_batch(),
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts={f"EDX{i}": _verdict(f"EDX{i}", "ok") for i in (1, 2, 3)},
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )
    raw = json.loads(
        (bundle_dir / "failure_taxonomy.json").read_text(encoding="utf-8")
    )
    assert raw == []
