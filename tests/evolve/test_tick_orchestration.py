"""Tick orchestration — read MOEX overlap and one full tick with mocked runner."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from edx.config import load_all
from edx.evolve import tick as tick_module
from edx.evolve.runner import PipelineRunResult
from edx.evolve.tick import _batch_improvement, read_moex_e_disclosure_ids
from edx.evolve.verdict import TickerVerdict


def _make_app_yaml(target: Path, state_db: Path, output: Path) -> None:
    target.write_text(
        f"paths:\n"
        f"  data_dir: {target.parent / 'data'}\n"
        f"  raw_dir: {target.parent / 'data/raw'}\n"
        f"  processed_dir: {target.parent / 'data/processed'}\n"
        f"  state_db: {state_db}\n"
        f"  output_dir: {output.parent}\n"
        f"  excel_path: {output}\n"
        f"  logs_dir: {target.parent / 'logs'}\n"
        f"schedule:\n  cron_time: '04:00'\n  timezone: Europe/Moscow\n"
        f"mode:\n  backfill_years: 3\n  default_run_mode: update\n"
        f"discoverer:\n  base_url: https://example.com\n"
        f"  requests_per_second: 1.0\n"
        f"  request_timeout_s: 30\n"
        f"  max_retries: 3\n"
        f"  retry_min_wait_s: 0.5\n"
        f"  retry_max_wait_s: 10\n"
        f"  respect_robots: true\n"
        f"  user_agent: null\n"
        f"  cookies: {{}}\n"
        f"  http_backend: httpx\n"
        f"downloader:\n  concurrency: 4\n  follow_html_links: false\n"
        f"  chunk_size_bytes: 65536\n"
        f"unpacker:\n  max_unpacked_mb: 500\n"
        f"classifier:\n  min_text_chars_per_page: 50\n"
        f"  min_text_chars: 400\n  first_pages_to_inspect: 3\n"
        f"text_extractor:\n  max_chars: 400000\n  extract_tables: true\n"
        f"  header_footer_min_pages: 3\n  issuer_trim_max_chars: 30000\n"
        f"  issuer_trim_min_section_chars: 500\n"
        f"  issuer_trim_toc_distance_chars: 3000\n"
        f"metric_extractor:\n  scan_ratio_threshold: 0.10\n"
        f"  pdf_input_standards: ['IFRS']\n"
        f"  balance_trim_max_chars: 200000\n"
        f"  vision_fallback_enabled: false\n"
        f"  vision_fallback_threshold: 0.5\n"
        f"  vision_fallback_max_pages: 8\n"
        f"  vision_only_global_disabled: false\n"
        f"  vision_only_max_pages_per_request: 25\n"
        f"validator:\n  completeness_threshold: 0.5\n"
        f"orchestrator:\n  publication_concurrency: 4\n"
        f"google_drive:\n  enabled: false\n  folder_id: null\n"
        f"  file_name: e-disclosure.xlsx\n  archive: false\n"
        f"contact_email: null\n",
        encoding="utf-8",
    )


def test_read_moex_skips_replace_me(tmp_path: Path) -> None:
    p = tmp_path / "tickers.yaml"
    p.write_text(
        "tickers:\n"
        "  - ticker: SBER\n    name: Сбер\n    e_disclosure_id: '3043'\n"
        "  - ticker: VTBR\n    name: ВТБ\n    e_disclosure_id: REPLACE_ME\n"
        "  - ticker: LKOH\n    name: ЛУКОЙЛ\n    e_disclosure_id: '17'\n",
        encoding="utf-8",
    )
    ids = read_moex_e_disclosure_ids(p)
    assert ids == frozenset({"3043", "17"})


def test_read_moex_handles_missing_file(tmp_path: Path) -> None:
    assert read_moex_e_disclosure_ids(tmp_path / "absent.yaml") == frozenset()


def test_read_moex_handles_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("not: [valid: yaml here", encoding="utf-8")
    assert read_moex_e_disclosure_ids(p) == frozenset()


def _setup_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Make a self-contained mini-project layout."""
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = proj / "config"
    cfg.mkdir()
    state_db = proj / "data" / "state.sqlite"
    output = proj / "output" / "e-disclosure.xlsx"
    _make_app_yaml(cfg / "app.yaml", state_db, output)

    real_cfg = Path("config")
    for name in ("metrics.yaml", "event_types.yaml", "ocr.yaml", "llm.yaml"):
        shutil.copy(real_cfg / name, cfg / name)
    (cfg / "tickers.yaml").write_text(
        "tickers:\n"
        "  - ticker: SBER\n    name: Сбербанк\n    e_disclosure_id: '3043'\n",
        encoding="utf-8",
    )

    # Companies CSV.
    csv = proj / "e-disclosure-companies.csv"
    csv.write_text(
        "id,name,type\n"
        "1,Co1,non_bank\n"
        "2,Co2,non_bank\n"
        "3,Co3,non_bank\n",
        encoding="utf-8",
    )
    return proj, cfg, csv


def test_run_one_tick_no_candidates_returns_zero(tmp_path: Path) -> None:
    proj, cfg, _csv = _setup_project(tmp_path)
    # Empty CSV (only header).
    empty_csv = proj / "empty.csv"
    empty_csv.write_text("id,name,type\n", encoding="utf-8")
    settings = load_all(cfg)
    out = tick_module.run_one_tick(
        settings,
        csv_path=empty_csv,
        main_tickers_yaml=cfg / "tickers.yaml",
        evolve_config_dir=proj / "config-evolve",
        bundle_root=proj / "evolution" / "runs",
    )
    assert out == 0


def test_run_one_tick_records_baseline(monkeypatch, tmp_path: Path) -> None:
    """End-to-end happy path with the pipeline subprocess mocked out."""
    # Isolate from production env: agent-enabled would try to create a git
    # branch (failing if evolve/tick-1 already exists); batch-size=1 would
    # cause the picker to return only 1 ticker instead of the expected 3.
    monkeypatch.delenv("EDX_EVOLVE_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("EDX_EVOLVE_BATCH_SIZE", raising=False)
    proj, cfg, csv = _setup_project(tmp_path)

    captured: dict[str, list[str]] = {}

    def fake_runner(
        tickers,
        *,
        config_dir,
        log_path,
        timeout_seconds=30 * 60,
        extra_env=None,
    ):  # type: ignore[no-untyped-def]
        captured["tickers"] = list(tickers)
        captured["config_dir"] = str(config_dir)
        # Pretend the pipeline ran and wrote one log line.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "event": "run_finished",
                    "level": "info",
                    "timestamp": "2026-05-03T10:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return PipelineRunResult(
            returncode=0,
            duration_seconds=0.5,
            stdout_tail="ok\n",
            stderr_tail="",
            log_path=log_path,
        )

    monkeypatch.setattr(tick_module, "run_pipeline_on_batch", fake_runner)

    settings = load_all(cfg)
    tick_id = tick_module.run_one_tick(
        settings,
        csv_path=csv,
        main_tickers_yaml=cfg / "tickers.yaml",
        evolve_config_dir=proj / "config-evolve",
        bundle_root=proj / "evolution" / "runs",
    )

    assert tick_id >= 1
    assert captured["tickers"] == ["EDX1", "EDX2", "EDX3"]
    bundle = proj / "evolution" / "runs" / str(tick_id)
    assert (bundle / "snap_before.json").exists()
    assert (bundle / "snap_after.json").exists()
    assert (bundle / "pipeline.log").exists()
    assert (bundle / "batch.json").exists()


def test_run_one_tick_honours_batch_size_env(
    monkeypatch, tmp_path: Path
) -> None:
    """``EDX_EVOLVE_BATCH_SIZE`` env var (loaded by systemd from
    ``/opt/edx/.env.evolve``) lets the operator drop batch size below
    the default of 3 — necessary on memory-constrained hosts where
    parallel processing of 3 defunct-company archive bootstraps OOM-kills
    the service. Anti-regression for production tick #79 OOM (May 4)."""
    proj, cfg, csv = _setup_project(tmp_path)

    captured: dict[str, list[str]] = {}

    def fake_runner(tickers, **kw):  # type: ignore[no-untyped-def]
        captured["tickers"] = list(tickers)
        log = kw["log_path"]
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        return PipelineRunResult(
            returncode=0,
            duration_seconds=0.1,
            stdout_tail="",
            stderr_tail="",
            log_path=log,
        )

    monkeypatch.setattr(tick_module, "run_pipeline_on_batch", fake_runner)
    monkeypatch.setenv(tick_module.BATCH_SIZE_ENV, "1")

    settings = load_all(cfg)
    tick_module.run_one_tick(
        settings,
        csv_path=csv,
        main_tickers_yaml=cfg / "tickers.yaml",
        evolve_config_dir=proj / "config-evolve",
        bundle_root=proj / "evolution" / "runs",
    )
    assert captured["tickers"] == ["EDX1"], (
        f"expected single ticker, got {captured['tickers']!r}"
    )


def test_run_one_tick_skips_moex_overlap(monkeypatch, tmp_path: Path) -> None:
    """A company id present in main config/tickers.yaml is excluded."""
    # Isolate from production env vars (same reasons as test_run_one_tick_records_baseline).
    monkeypatch.delenv("EDX_EVOLVE_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("EDX_EVOLVE_BATCH_SIZE", raising=False)
    proj, cfg, _csv = _setup_project(tmp_path)
    csv = proj / "csv_with_overlap.csv"
    csv.write_text(
        "id,name,type\n"
        "3043,Sber,bank\n"     # ← MOEX overlap (in main tickers.yaml)
        "1,Co1,non_bank\n"
        "2,Co2,non_bank\n"
        "3,Co3,non_bank\n",
        encoding="utf-8",
    )

    captured: dict[str, list[str]] = {}

    def fake_runner(tickers, **kw):  # type: ignore[no-untyped-def]
        captured["tickers"] = list(tickers)
        log = kw["log_path"]
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        return PipelineRunResult(
            returncode=0,
            duration_seconds=0.1,
            stdout_tail="",
            stderr_tail="",
            log_path=log,
        )

    monkeypatch.setattr(tick_module, "run_pipeline_on_batch", fake_runner)

    settings = load_all(cfg)
    tick_module.run_one_tick(
        settings,
        csv_path=csv,
        main_tickers_yaml=cfg / "tickers.yaml",
        evolve_config_dir=proj / "config-evolve",
        bundle_root=proj / "evolution" / "runs",
    )
    # 3043 must be missing from the run.
    assert "EDX3043" not in captured["tickers"]
    assert captured["tickers"] == ["EDX1", "EDX2", "EDX3"]


# ---------------------------------------------------------------------------
# _batch_improvement unit tests
# ---------------------------------------------------------------------------


def _make_verdict(ticker: str, code: str) -> TickerVerdict:
    return TickerVerdict(
        ticker=ticker,
        code=code,  # type: ignore[arg-type]
        metrics_delta=0,
        publications_written_delta=0,
        qa_issues_delta=0,
        notes=(),
    )


def test_batch_improvement_fail_to_ok_counts_as_improved() -> None:
    before = {"EDX1": _make_verdict("EDX1", "fail")}
    after = {"EDX1": _make_verdict("EDX1", "ok")}
    improved, not_regressed = _batch_improvement(before, after)
    assert improved is True
    assert not_regressed is True


def test_batch_improvement_neutral_to_ok_counts_as_improved() -> None:
    """neutral → ok must count as improvement so tickers with invalid
    e_disclosure_ids that get corrected by the operator can pass the gate."""
    before = {"EDX1": _make_verdict("EDX1", "neutral")}
    after = {"EDX1": _make_verdict("EDX1", "ok")}
    improved, not_regressed = _batch_improvement(before, after)
    assert improved is True
    assert not_regressed is True


def test_batch_improvement_neutral_to_neutral_not_improved() -> None:
    before = {"EDX1": _make_verdict("EDX1", "neutral")}
    after = {"EDX1": _make_verdict("EDX1", "neutral")}
    improved, not_regressed = _batch_improvement(before, after)
    assert improved is False
    assert not_regressed is True


def test_batch_improvement_ok_to_ok_not_counted_as_improvement() -> None:
    """A ticker that was already ok and stays ok should not count as improvement."""
    before = {"EDX1": _make_verdict("EDX1", "ok")}
    after = {"EDX1": _make_verdict("EDX1", "ok")}
    improved, not_regressed = _batch_improvement(before, after)
    assert improved is False
    assert not_regressed is True


def test_batch_improvement_regression_detected() -> None:
    before = {"EDX1": _make_verdict("EDX1", "fail")}
    after = {"EDX1": _make_verdict("EDX1", "regression")}
    improved, not_regressed = _batch_improvement(before, after)
    assert improved is False
    assert not_regressed is False
