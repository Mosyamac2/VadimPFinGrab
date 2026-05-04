"""Failure taxonomy classifier (Patch 41)."""

from __future__ import annotations

import json
from pathlib import Path

from edx.evolve.taxonomy import classify_failures


def _write_log(path: Path, lines: list[dict]) -> None:
    payload = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
    path.write_text(payload + "\n", encoding="utf-8")


def test_classify_servicepipe_403(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "discoverer_non_200",
                "level": "warning",
                "ticker": "EDX1",
                "url": "/portal/files.aspx?id=1&type=4",
                "status": 403,
            }
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "discoverer_403_servicepipe"
    assert out[0].evidence  # non-empty


def test_classify_no_publications_for_all_4_types(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "discoverer_no_publications_for_type",
                "level": "info",
                "ticker": "EDX1",
                "type_code": tc,
            }
            for tc in (2, 3, 4, 5)
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "discoverer_no_publications"


def test_classify_unique_constraint(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "metric_extract_failed",
                "level": "error",
                "ticker": "EDX1",
                "publication_id": "P1",
                "error": "UNIQUE constraint failed: metrics.ticker, ...",
                "exc_type": "IntegrityError",
            }
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "unique_constraint"


def test_classify_period_unparseable(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "period_parser_unmatched",
                "level": "warning",
                "ticker": "EDX1",
                "detail": "could not parse",
            }
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "period_unparseable"


def test_classify_pipeline_timeout_before_metric_extract(tmp_path: Path) -> None:
    """publication_extracted events but no metric_extract_* → pipeline_timeout."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "publication_extracted",
                "level": "info",
                "publication_id": "EDX1-3-999",
                "documents_processed": 1,
                "native": 0,
                "ocr": 1,
                "total_chars": 40000,
            }
        ],
    )
    state_slice = {
        "EDX1": {
            "documents": [{"is_machine_readable": 1, "reporting_standard": "RSBU"}],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    assert out[0].code == "pipeline_timeout"
    assert out[0].evidence  # shows which publication was extracted


def test_classify_metric_coverage_zero_not_pipeline_timeout(tmp_path: Path) -> None:
    """If metric_extract_* events exist, pipeline ran → not a timeout."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "publication_extracted",
                "level": "info",
                "publication_id": "EDX1-3-999",
                "documents_processed": 1,
                "native": 0,
                "ocr": 1,
                "total_chars": 40000,
            },
            {
                "event": "metric_extract_start",
                "level": "info",
                "publication_id": "EDX1-3-999",
            },
            {
                "event": "metric_extract_completed",
                "level": "info",
                "publication_id": "EDX1-3-999",
                "rows_written": 0,
            },
        ],
    )
    state_slice = {
        "EDX1": {
            "documents": [{"is_machine_readable": 1, "reporting_standard": "RSBU"}],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    # metric extractor ran → not pipeline_timeout → metric_coverage_zero
    assert out[0].code == "metric_coverage_zero"


def test_classify_pipeline_timeout_does_not_smear_across_tickers(
    tmp_path: Path,
) -> None:
    """EDX1 extraction events must not trigger pipeline_timeout for EDX2."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "publication_extracted",
                "level": "info",
                "publication_id": "EDX1-3-999",
                "documents_processed": 1,
                "native": 1,
                "ocr": 0,
                "total_chars": 30000,
            }
        ],
    )
    state_slice = {
        "EDX1": {
            "documents": [{"is_machine_readable": 1, "reporting_standard": "RSBU"}],
            "metrics_count": 0,
            "qa_issues": [],
        },
        "EDX2": {
            "documents": [{"is_machine_readable": 1, "reporting_standard": "RSBU"}],
            "metrics_count": 0,
            "qa_issues": [],
        },
    }
    out = classify_failures(
        log, state_slice=state_slice, failing_tickers=["EDX1", "EDX2"]
    )
    assert out[0].code == "pipeline_timeout"  # EDX1 has extracted events
    assert out[1].code != "pipeline_timeout"  # EDX2 has no extracted events


def test_classify_metric_coverage_zero_from_state(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    state_slice = {
        "EDX1": {
            "documents": [{"is_machine_readable": 1, "reporting_standard": "IFRS"}],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    assert out[0].code == "metric_coverage_zero"


def test_classify_oom_kill_when_returncode_minus_9(tmp_path: Path) -> None:
    """returncode=-9 (SIGKILL/OOM) + extraction events + no metric events → oom_kill."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "publication_extracted",
                "level": "info",
                "publication_id": "EDX1-3-999",
                "documents_processed": 1,
                "native": 0,
                "ocr": 1,
                "total_chars": 40000,
            }
        ],
    )
    state_slice = {
        "EDX1": {
            "documents": [{"is_machine_readable": 1, "reporting_standard": "RSBU"}],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(
        log,
        state_slice=state_slice,
        failing_tickers=["EDX1"],
        pipeline_returncode=-9,
    )
    assert out[0].code == "oom_kill"
    assert out[0].evidence  # shows which publication was extracted


def test_classify_pipeline_timeout_not_oom_when_returncode_minus_1(
    tmp_path: Path,
) -> None:
    """returncode=-1 (Python TimeoutExpired) + same log pattern → pipeline_timeout."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "publication_extracted",
                "level": "info",
                "publication_id": "EDX1-3-999",
                "documents_processed": 1,
                "native": 1,
                "ocr": 0,
                "total_chars": 30000,
            }
        ],
    )
    state_slice = {
        "EDX1": {
            "documents": [{"is_machine_readable": 1, "reporting_standard": "RSBU"}],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(
        log,
        state_slice=state_slice,
        failing_tickers=["EDX1"],
        pipeline_returncode=-1,
    )
    assert out[0].code == "pipeline_timeout"
    assert out[0].code != "oom_kill"


def test_classify_metric_coverage_low_from_qa_incomplete(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    state_slice = {
        "EDX1": {
            "documents": [{"is_machine_readable": 1}],
            "metrics_count": 2,
            "qa_issues": [{"code": "incomplete", "message": "low"}],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    assert out[0].code == "metric_coverage_low"


def test_classify_classifier_other(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    state_slice = {
        "EDX1": {
            "documents": [
                {"reporting_standard": "OTHER"},
                {"reporting_standard": "OTHER"},
            ],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    # metric_coverage_zero would need is_machine_readable=1, so this falls
    # into classifier_other.
    assert out[0].code == "classifier_other"


def test_classify_pipeline_crashed(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "orchestrator_replicator_failed",
                "level": "error",
                "exc_type": "RuntimeError",
                "error": "boom",
            }
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "pipeline_crashed"


def test_classify_no_recent_publications(tmp_path: Path) -> None:
    """Company found on website but all publications predate the cutoff."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "ticker_type_discovered",
                "level": "info",
                "ticker": "EDX1",
                "type_code": 3,
                "cutoff_date": "2025-05-03",
                "found": 18,
                "new": 0,
                "inserted": 0,
            },
            {
                "event": "ticker_type_discovered",
                "level": "info",
                "ticker": "EDX1",
                "type_code": 5,
                "cutoff_date": "2025-05-03",
                "found": 72,
                "new": 0,
                "inserted": 0,
            },
        ],
    )
    state = {"EDX1": {"publications": [], "metrics_count": 0}}
    out = classify_failures(log, state_slice=state, failing_tickers=["EDX1"])
    assert out[0].code == "no_recent_publications"
    assert out[0].evidence  # non-empty evidence with found/new counts


def test_classify_row_structure_warning_not_period_unparseable(tmp_path: Path) -> None:
    """discoverer_parse_warning about row structure must not → period_unparseable."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "discoverer_parse_warning",
                "level": "warning",
                "ticker": "EDX1",
                "type_code": 5,
                "detail": "row with only 1 cells (expected ≥6): часть 1",
            }
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code != "period_unparseable"


def test_classify_period_warning_in_detail_still_triggers_period_unparseable(
    tmp_path: Path,
) -> None:
    """discoverer_parse_warning with 'reporting period' in detail → period_unparseable."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "discoverer_parse_warning",
                "level": "warning",
                "ticker": "EDX1",
                "type_code": 3,
                "detail": "unrecognised reporting period '2025 г' for fileid=12345",
            }
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "period_unparseable"


def test_classify_cli_startup_error_empty_log_no_state(tmp_path: Path) -> None:
    """Empty pipeline.log + no state → cli_startup_error (argparse/startup crash)."""
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "cli_startup_error"
    assert out[0].hint  # always non-empty


def test_classify_cli_startup_error_three_tickers_all_empty(tmp_path: Path) -> None:
    """All three tickers with empty log + empty state → cli_startup_error for each."""
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    state_slice = {
        "EDX1021": {"publications": [], "metrics_count": 0},
        "EDX105": {"publications": [], "metrics_count": 0},
        "EDX11473": {"publications": [], "metrics_count": 0},
    }
    out = classify_failures(
        log,
        state_slice=state_slice,
        failing_tickers=["EDX1021", "EDX105", "EDX11473"],
    )
    assert all(e.code == "cli_startup_error" for e in out)


def test_classify_unknown_when_log_empty_but_has_publications(tmp_path: Path) -> None:
    """Empty log but publications exist → not a startup crash, fall through to unknown."""
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    state = {"EDX1": {"publications": [{"publication_id": "EDX1-3-1"}], "metrics_count": 0}}
    out = classify_failures(log, state_slice=state, failing_tickers=["EDX1"])
    assert out[0].code == "unknown"
    assert out[0].hint  # always non-empty


def test_classify_unknown_when_log_empty_but_has_documents(tmp_path: Path) -> None:
    """Empty log but documents exist in state → not a startup crash, fall through."""
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    state = {"EDX1": {"documents": [{"reporting_standard": "RSBU"}], "metrics_count": 0}}
    out = classify_failures(log, state_slice=state, failing_tickers=["EDX1"])
    assert out[0].code != "cli_startup_error"


def test_classify_skips_malformed_lines(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text(
        "{this is not valid json}\n"
        + json.dumps(
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
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "discoverer_403_servicepipe"


def test_classify_returns_one_entry_per_ticker(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "discoverer_non_200",
                "level": "warning",
                "ticker": "EDX1",
                "status": 403,
            },
        ],
    )
    out = classify_failures(
        log,
        state_slice={"EDX1": {}, "EDX2": {}},
        failing_tickers=["EDX1", "EDX2"],
    )
    assert [e.ticker for e in out] == ["EDX1", "EDX2"]
    assert out[0].code == "discoverer_403_servicepipe"
    # EDX2 has nothing → unknown.
    assert out[1].code == "unknown"
