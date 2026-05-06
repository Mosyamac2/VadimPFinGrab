"""Diagnostic Bundle assembly (Patch 41)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from edx.config import TickerEntry
from edx.evolve import bundle as bundle_module
from edx.evolve.bundle import (
    _has_llm_stuck_publications,
    _has_written_no_metrics_publications,
    _has_zero_publications,
)
from edx.evolve.csv_loader import CompanyRow
from edx.evolve.snapshot import TickerSnapshot
from edx.evolve.verdict import TickerVerdict
from edx.storage import Database, PublicationsRepo, TickersRepo


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


_HTTP_402_ERROR = 'openrouter HTTP 402: {"error":{"message":"Insufficient credits."}}'


def test_has_llm_stuck_publications_true_when_http_402_in_last_error() -> None:
    """_has_llm_stuck_publications returns True for a ticker with publications
    stuck in 'failed' status due to HTTP 402 errors."""
    state_slice = {
        "EDX1": {
            "publications": [
                {
                    "publication_id": "EDX1-3-100",
                    "status": "failed",
                    "last_error": _HTTP_402_ERROR,
                }
            ]
        }
    }
    assert _has_llm_stuck_publications(state_slice, "EDX1") is True


def test_has_llm_stuck_publications_false_when_no_http_402() -> None:
    state_slice = {
        "EDX1": {
            "publications": [
                {
                    "publication_id": "EDX1-3-100",
                    "status": "failed",
                    "last_error": "zipfile.BadZipFile: Bad CRC-32",
                }
            ]
        }
    }
    assert _has_llm_stuck_publications(state_slice, "EDX1") is False


def test_has_llm_stuck_publications_false_when_no_failed_pubs() -> None:
    state_slice = {
        "EDX1": {
            "publications": [
                {
                    "publication_id": "EDX1-3-100",
                    "status": "written",
                    "last_error": None,
                }
            ]
        }
    }
    assert _has_llm_stuck_publications(state_slice, "EDX1") is False


def test_bundle_neutral_ticker_with_stuck_llm_pubs_included_in_failing(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A neutral-verdict ticker whose publications are stuck in 'failed' due to
    HTTP 402 should appear in failing_tickers and receive llm_failed_stuck
    taxonomy classification."""
    bundle_dir = tmp_path / "stuck"
    log_path = tmp_path / "p.log"
    log_path.write_text("", encoding="utf-8")

    # Seed ticker then publication stuck in 'failed' with HTTP 402 for EDX1.
    TickersRepo(evolve_db, evolve_conn).upsert_from_config(
        [TickerEntry(ticker="EDX1", e_disclosure_id="1", name="Co1")]
    )
    repo = PublicationsRepo(evolve_db, evolve_conn)
    repo.upsert_discovered(
        publication_id="EDX1-3-999",
        ticker="EDX1",
        publication_type="report",
        publication_date="2026-01-01",
        source_url="https://example.test/r.zip",
    )
    for status in ("downloaded", "unpacked", "classified", "extracted"):
        repo.mark_status("EDX1-3-999", status)  # type: ignore[arg-type]
    repo.mark_status(
        "EDX1-3-999",
        "failed",
        error=_HTTP_402_ERROR,
    )

    batch = _make_batch()
    verdicts = {
        "EDX1": _verdict("EDX1", "neutral"),
        "EDX2": _verdict("EDX2", "ok"),
        "EDX3": _verdict("EDX3", "ok"),
    }

    manifest = bundle_module.assemble(
        bundle_dir,
        batch=batch,
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts=verdicts,
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )

    # The neutral ticker with stuck publications must appear in failing_tickers.
    assert "EDX1" in manifest["failing_tickers"]
    assert "EDX2" not in manifest["failing_tickers"]

    # And the taxonomy must classify it as llm_failed_stuck.
    raw_tax = json.loads(
        (bundle_dir / "failure_taxonomy.json").read_text(encoding="utf-8")
    )
    tax_by_ticker = {e["ticker"]: e["code"] for e in raw_tax}
    assert tax_by_ticker.get("EDX1") == "llm_failed_stuck"


def test_has_zero_publications_true_when_empty_list() -> None:
    state_slice: dict[str, dict[str, object]] = {
        "EDX99": {"publications": [], "documents": [], "metrics": [], "qa_issues": []}
    }
    assert _has_zero_publications(state_slice, "EDX99") is True


def test_has_zero_publications_false_when_has_publications() -> None:
    state_slice: dict[str, dict[str, object]] = {
        "EDX99": {
            "publications": [{"publication_id": "EDX99-3-1", "status": "discovered"}],
            "documents": [],
            "metrics": [],
            "qa_issues": [],
        }
    }
    assert _has_zero_publications(state_slice, "EDX99") is False


def test_has_zero_publications_true_when_ticker_missing_from_slice() -> None:
    state_slice: dict[str, dict[str, object]] = {}
    assert _has_zero_publications(state_slice, "EDX99") is True


def test_bundle_neutral_ticker_with_zero_publications_included_in_failing(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A neutral-verdict ticker with zero publications (portal returned 404 for
    all types — invalid e_disclosure_id) must appear in failing_tickers so the
    taxonomy fires discoverer_id_not_found."""
    bundle_dir = tmp_path / "zero_pubs"
    log_path = tmp_path / "p.log"
    log_lines = [
        json.dumps({
            "event": "discoverer_no_publications_for_type",
            "level": "info",
            "ticker": "EDX1",
            "type_code": tc,
            "status": 404,
        })
        for tc in (2, 3, 4, 5)
    ]
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    batch = _make_batch()
    verdicts = {
        "EDX1": _verdict("EDX1", "neutral"),
        "EDX2": _verdict("EDX2", "ok"),
        "EDX3": _verdict("EDX3", "ok"),
    }

    manifest = bundle_module.assemble(
        bundle_dir,
        batch=batch,
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts=verdicts,
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )

    assert "EDX1" in manifest["failing_tickers"]
    assert "EDX2" not in manifest["failing_tickers"]

    raw_tax = json.loads(
        (bundle_dir / "failure_taxonomy.json").read_text(encoding="utf-8")
    )
    tax_by_ticker = {e["ticker"]: e["code"] for e in raw_tax}
    assert tax_by_ticker.get("EDX1") == "discoverer_id_not_found"


# -------- _has_written_no_metrics_publications unit tests --------


def test_has_written_no_metrics_true_when_all_written_zero_metrics() -> None:
    state_slice: dict[str, dict[str, object]] = {
        "EDX1482": {
            "publications": [{"publication_id": "EDX1482-3-194054", "status": "written"}],
            "metrics_count": 0,
        }
    }
    assert _has_written_no_metrics_publications(state_slice, "EDX1482") is True


def test_has_written_no_metrics_false_when_metrics_exist() -> None:
    state_slice: dict[str, dict[str, object]] = {
        "EDX1": {
            "publications": [{"publication_id": "EDX1-3-1", "status": "written"}],
            "metrics_count": 5,
        }
    }
    assert _has_written_no_metrics_publications(state_slice, "EDX1") is False


def test_has_written_no_metrics_false_when_not_all_written() -> None:
    state_slice: dict[str, dict[str, object]] = {
        "EDX1": {
            "publications": [
                {"publication_id": "EDX1-3-1", "status": "written"},
                {"publication_id": "EDX1-3-2", "status": "extracted"},
            ],
            "metrics_count": 0,
        }
    }
    assert _has_written_no_metrics_publications(state_slice, "EDX1") is False


def test_has_written_no_metrics_false_when_empty_pubs() -> None:
    state_slice: dict[str, dict[str, object]] = {
        "EDX1": {"publications": [], "metrics_count": 0}
    }
    assert _has_written_no_metrics_publications(state_slice, "EDX1") is False


def test_bundle_neutral_ticker_with_written_no_metrics_included_in_failing(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A neutral-verdict ticker with all publications written but 0 metrics
    must appear in failing_tickers so the taxonomy fires llm_arg_too_long."""
    bundle_dir = tmp_path / "written_no_metrics"
    log_path = tmp_path / "p.log"
    e2big_err = (
        "claude_code: cannot spawn '/usr/bin/claude': "
        "[Errno 7] Argument list too long: '/usr/bin/claude'"
    )
    _write_log = lambda lines: log_path.write_text(  # noqa: E731
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    _write_log(
        [
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-3-194054",
                "error": e2big_err,
            }
        ]
    )

    # Seed a written publication with 0 metrics in the DB.
    TickersRepo(evolve_db, evolve_conn).upsert_from_config(
        [TickerEntry(ticker="EDX1", e_disclosure_id="1", name="Co1")]
    )
    repo = PublicationsRepo(evolve_db, evolve_conn)
    repo.upsert_discovered(
        publication_id="EDX1-3-194054",
        ticker="EDX1",
        publication_type="report",
        publication_date="2012-08-15",
        source_url="https://example.test/r.zip",
    )
    for status in ("downloaded", "unpacked", "classified", "extracted", "validated", "written"):
        repo.mark_status("EDX1-3-194054", status)  # type: ignore[arg-type]

    batch = _make_batch()
    verdicts = {
        "EDX1": _verdict("EDX1", "neutral"),
        "EDX2": _verdict("EDX2", "ok"),
        "EDX3": _verdict("EDX3", "ok"),
    }

    manifest = bundle_module.assemble(
        bundle_dir,
        batch=batch,
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts=verdicts,
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )

    assert "EDX1" in manifest["failing_tickers"]
    assert "EDX2" not in manifest["failing_tickers"]

    raw_tax = json.loads(
        (bundle_dir / "failure_taxonomy.json").read_text(encoding="utf-8")
    )
    tax_by_ticker = {e["ticker"]: e["code"] for e in raw_tax}
    assert tax_by_ticker.get("EDX1") == "llm_arg_too_long"


# -------- _has_written_no_metrics: mixed written+skipped cases --------


def test_has_written_no_metrics_true_when_mixed_written_and_skipped() -> None:
    """Mixed written+skipped, 0 metrics → True (EDX20321 scenario)."""
    state_slice: dict[str, dict[str, object]] = {
        "EDX20321": {
            "publications": [
                {"publication_id": "EDX20321-3-1", "status": "written"},
                {"publication_id": "EDX20321-3-2", "status": "written"},
                {"publication_id": "EDX20321-2-3", "status": "skipped"},
                {"publication_id": "EDX20321-2-4", "status": "skipped"},
            ],
            "metrics_count": 0,
        }
    }
    assert _has_written_no_metrics_publications(state_slice, "EDX20321") is True


def test_has_written_no_metrics_true_when_all_skipped_zero_metrics() -> None:
    """All publications skipped, 0 metrics → True."""
    state_slice: dict[str, dict[str, object]] = {
        "EDX1": {
            "publications": [
                {"publication_id": "EDX1-2-1", "status": "skipped"},
                {"publication_id": "EDX1-2-2", "status": "skipped"},
            ],
            "metrics_count": 0,
        }
    }
    assert _has_written_no_metrics_publications(state_slice, "EDX1") is True


def test_has_written_no_metrics_false_when_some_not_terminal() -> None:
    """written + extracted (non-terminal) → False; not all publications are terminal."""
    state_slice: dict[str, dict[str, object]] = {
        "EDX1": {
            "publications": [
                {"publication_id": "EDX1-3-1", "status": "written"},
                {"publication_id": "EDX1-3-2", "status": "extracted"},
            ],
            "metrics_count": 0,
        }
    }
    assert _has_written_no_metrics_publications(state_slice, "EDX1") is False


def test_bundle_neutral_ticker_with_mixed_terminal_included_in_failing(
    evolve_db: Database, evolve_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A neutral-verdict ticker with mixed written+skipped and 0 metrics must
    appear in failing_tickers so the taxonomy fires all_terminal_no_metrics."""
    bundle_dir = tmp_path / "mixed_terminal"
    log_path = tmp_path / "p.log"
    log_path.write_text("", encoding="utf-8")

    TickersRepo(evolve_db, evolve_conn).upsert_from_config(
        [TickerEntry(ticker="EDX1", e_disclosure_id="1", name="Co1")]
    )
    repo = PublicationsRepo(evolve_db, evolve_conn)

    # Seed one written and one skipped publication with 0 metrics.
    repo.upsert_discovered(
        publication_id="EDX1-3-1",
        ticker="EDX1",
        publication_type="report",
        publication_date="2012-08-15",
        source_url="https://example.test/r1.zip",
    )
    for status in ("downloaded", "unpacked", "classified", "extracted", "validated", "written"):
        repo.mark_status("EDX1-3-1", status)  # type: ignore[arg-type]

    repo.upsert_discovered(
        publication_id="EDX1-2-2",
        ticker="EDX1",
        publication_type="report",
        publication_date="2012-08-15",
        source_url="https://example.test/r2.zip",
    )
    for status in ("downloaded", "unpacked", "classified", "extracted", "validated", "skipped"):
        repo.mark_status("EDX1-2-2", status)  # type: ignore[arg-type]

    batch = _make_batch()
    verdicts = {
        "EDX1": _verdict("EDX1", "neutral"),
        "EDX2": _verdict("EDX2", "ok"),
        "EDX3": _verdict("EDX3", "ok"),
    }

    manifest = bundle_module.assemble(
        bundle_dir,
        batch=batch,
        snaps_before={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        snaps_after={f"EDX{i}": _empty_snap(f"EDX{i}") for i in (1, 2, 3)},
        verdicts=verdicts,
        log_path=log_path,
        state_db=Path(evolve_db.path),
        conn=evolve_conn,
    )

    assert "EDX1" in manifest["failing_tickers"]
    assert "EDX2" not in manifest["failing_tickers"]

    raw_tax = json.loads(
        (bundle_dir / "failure_taxonomy.json").read_text(encoding="utf-8")
    )
    tax_by_ticker = {e["ticker"]: e["code"] for e in raw_tax}
    assert tax_by_ticker.get("EDX1") == "all_terminal_no_metrics"
