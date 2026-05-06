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
                "status": 200,
            }
            for tc in (2, 3, 4, 5)
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "discoverer_no_publications"


def test_classify_discoverer_id_not_found_when_all_404(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "discoverer_no_publications_for_type",
                "level": "info",
                "ticker": "EDX1",
                "type_code": tc,
                "status": 404,
            }
            for tc in (2, 3, 4, 5)
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "discoverer_id_not_found"
    assert "404" in out[0].evidence[0]


def test_classify_discoverer_id_not_found_when_all_410(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "discoverer_no_publications_for_type",
                "level": "info",
                "ticker": "EDX1",
                "type_code": tc,
                "status": 410,
            }
            for tc in (2, 3, 4, 5)
        ],
    )
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "discoverer_id_not_found"


def test_classify_no_publications_when_mixed_status(tmp_path: Path) -> None:
    """Three 404s and one 200 should classify as discoverer_no_publications."""
    log = tmp_path / "p.log"
    lines = [
        {
            "event": "discoverer_no_publications_for_type",
            "level": "info",
            "ticker": "EDX1",
            "type_code": tc,
            "status": 404,
        }
        for tc in (2, 3, 4)
    ]
    lines.append(
        {
            "event": "discoverer_no_publications_for_type",
            "level": "info",
            "ticker": "EDX1",
            "type_code": 5,
            "status": 200,
        }
    )
    _write_log(log, lines)
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


def test_classify_llm_arg_too_long(tmp_path: Path) -> None:
    """metric_extract_llm_unavailable with 'Argument list too long' in error
    → llm_arg_too_long, not llm_credits_exhausted."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-3-194054",
                "error": (
                    "claude_code: cannot spawn '/usr/bin/claude': "
                    "[Errno 7] Argument list too long: '/usr/bin/claude'"
                ),
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
    assert out[0].code == "llm_arg_too_long"
    assert out[0].evidence


def test_classify_llm_arg_too_long_takes_precedence_over_llm_credits_exhausted(
    tmp_path: Path,
) -> None:
    """Rule 4.65 must fire before rule 4.75 when 'Argument list too long' is present."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-3-194054",
                "error": (
                    "claude_code: cannot spawn '/usr/bin/claude': "
                    "[Errno 7] Argument list too long"
                ),
            },
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-4-9999",
                "error": "openrouter HTTP 402: Insufficient credits",
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
    assert out[0].code == "llm_arg_too_long"


def test_classify_llm_credits_exhausted(tmp_path: Path) -> None:
    """metric_extract_llm_unavailable events for the ticker → llm_credits_exhausted,
    not metric_coverage_zero (which would suggest 'extend synonyms' unhelpfully)."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-3-999",
                "error": "openrouter HTTP 402: Insufficient credits",
            },
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-4-888",
                "error": "openrouter HTTP 402: Insufficient credits",
            },
            {
                "event": "llm_chain_exhausted",
                "level": "error",
                "last_provider": "openrouter",
                "error": "openrouter HTTP 402: Insufficient credits",
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
    assert out[0].code == "llm_credits_exhausted"
    assert out[0].evidence  # shows which publications failed


def test_classify_llm_credits_exhausted_does_not_smear_across_tickers(
    tmp_path: Path,
) -> None:
    """llm_unavailable events for EDX1 must not classify EDX2 as llm_credits_exhausted."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-3-999",
                "error": "openrouter HTTP 402: Insufficient credits",
            },
        ],
    )
    state_slice = {
        "EDX1": {"documents": [{"is_machine_readable": 1}], "metrics_count": 0, "qa_issues": []},
        "EDX2": {"documents": [{"is_machine_readable": 1}], "metrics_count": 0, "qa_issues": []},
    }
    out = classify_failures(
        log, state_slice=state_slice, failing_tickers=["EDX1", "EDX2"]
    )
    assert out[0].code == "llm_credits_exhausted"
    assert out[1].code != "llm_credits_exhausted"


_LLM_402_ERR = "openrouter HTTP 402: Insufficient credits"


def test_classify_llm_failed_stuck(tmp_path: Path) -> None:
    """Publications stuck in 'failed' status with HTTP 402 and no
    metric_extract_* events in the log → llm_failed_stuck (not unknown).
    This happens when a prior run marked publications as 'failed' on
    LLMUnavailableError; the metric_extractor skips them on every subsequent
    run because it only processes 'extracted' publications."""
    log = tmp_path / "p.log"
    # The pipeline ran (discoverer events present) but metric_extractor never
    # fired (no metric_extract_* events) because all publications were already
    # in 'failed' status.
    _write_log(
        log,
        [
            {
                "ticker": "EDX1",
                "event": "ticker_type_discovered",
                "level": "info",
                "found": 10,
                "new": 0,
                "inserted": 0,
            },
        ],
    )
    state_slice = {
        "EDX1": {
            "publications": [
                {
                    "publication_id": "EDX1-3-100",
                    "status": "failed",
                    "last_error": _LLM_402_ERR,
                },
                {
                    "publication_id": "EDX1-4-101",
                    "status": "failed",
                    "last_error": _LLM_402_ERR,
                },
            ],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    assert out[0].code == "llm_failed_stuck"
    assert out[0].evidence  # lists the stuck publication IDs


def test_classify_llm_failed_stuck_does_not_fire_when_metric_extract_ran(
    tmp_path: Path,
) -> None:
    """If metric_extract_* events exist in the log (extractor DID run this
    tick), do NOT classify as llm_failed_stuck — the live HTTP 402 is
    better described by llm_credits_exhausted."""
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "event": "metric_extract_llm_unavailable",
                "level": "error",
                "publication_id": "EDX1-3-100",
                "error": _LLM_402_ERR,
            },
        ],
    )
    state_slice = {
        "EDX1": {
            "publications": [
                {
                    "publication_id": "EDX1-3-100",
                    "status": "failed",
                    "last_error": _LLM_402_ERR,
                },
            ],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    # metric_extract_llm_unavailable is present → llm_credits_exhausted wins,
    # not llm_failed_stuck (even though state_slice also shows failed pubs).
    assert out[0].code == "llm_credits_exhausted"


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


def test_classify_all_terminal_no_metrics(tmp_path: Path) -> None:
    """All publications in {written, skipped} with 0 metrics → all_terminal_no_metrics.

    Models EDX20321: 19 written (RSBU scans, LLM found 0 metrics) + 27 skipped
    (type-2 annual reports, no IFRS/RSBU/ISSUER docs in the publication).
    """
    log = tmp_path / "p.log"
    _write_log(
        log,
        [
            {
                "ticker": "EDX20321",
                "event": "ticker_type_discovered",
                "level": "info",
                "found": 19,
                "new": 0,
                "inserted": 0,
            },
        ],
    )
    state_slice = {
        "EDX20321": {
            "publications": [
                {"publication_id": "EDX20321-3-1", "status": "written"},
                {"publication_id": "EDX20321-3-2", "status": "written"},
                {"publication_id": "EDX20321-2-3", "status": "skipped"},
                {"publication_id": "EDX20321-2-4", "status": "skipped"},
            ],
            "documents": [
                {"document_id": 1, "is_machine_readable": 0, "reporting_standard": "RSBU"},
            ],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX20321"])
    assert out[0].code == "all_terminal_no_metrics"
    assert "written=2" in out[0].evidence[0]
    assert "skipped=2" in out[0].evidence[0]


def test_classify_all_terminal_no_metrics_takes_precedence_over_metric_coverage_zero(
    tmp_path: Path,
) -> None:
    """all_terminal_no_metrics (rule 4.8) fires before metric_coverage_zero (rule 5).

    Even when there are machine-readable documents (which would normally trigger
    metric_coverage_zero), if all pubs are in terminal states rule 4.8 wins.
    """
    log = tmp_path / "p.log"
    _write_log(log, [])
    state_slice = {
        "EDX1": {
            "publications": [
                {"publication_id": "EDX1-3-1", "status": "written"},
                {"publication_id": "EDX1-2-2", "status": "skipped"},
            ],
            "documents": [
                {"document_id": 1, "is_machine_readable": 1, "reporting_standard": "RSBU"},
            ],
            "metrics_count": 0,
            "qa_issues": [],
        }
    }
    out = classify_failures(log, state_slice=state_slice, failing_tickers=["EDX1"])
    assert out[0].code == "all_terminal_no_metrics", (
        f"expected all_terminal_no_metrics, got {out[0].code}"
    )
