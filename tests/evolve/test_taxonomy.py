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


def test_classify_unknown_when_log_empty(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text("", encoding="utf-8")
    out = classify_failures(log, state_slice={"EDX1": {}}, failing_tickers=["EDX1"])
    assert out[0].code == "unknown"
    assert out[0].hint  # always non-empty


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
