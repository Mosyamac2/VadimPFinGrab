"""edx evolve CLI subcommands (Patch 43)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from edx.cli import main


def _setup_project(tmp_path: Path) -> Path:
    """Mini project: copy real config/* (minus tickers.yaml) and write a
    minimal app.yaml that points to a tmp state.sqlite."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    real_cfg = Path("config")
    for name in ("metrics.yaml", "event_types.yaml", "ocr.yaml", "llm.yaml"):
        shutil.copy(real_cfg / name, cfg / name)

    state_db = tmp_path / "data" / "state.sqlite"
    output = tmp_path / "output" / "e-disclosure.xlsx"
    (cfg / "tickers.yaml").write_text(
        "tickers:\n"
        "  - ticker: SBER\n    name: Sber\n    e_disclosure_id: '3043'\n",
        encoding="utf-8",
    )
    (cfg / "app.yaml").write_text(
        f"paths:\n"
        f"  data_dir: {tmp_path / 'data'}\n"
        f"  raw_dir: {tmp_path / 'data/raw'}\n"
        f"  processed_dir: {tmp_path / 'data/processed'}\n"
        f"  state_db: {state_db}\n"
        f"  output_dir: {output.parent}\n"
        f"  excel_path: {output}\n"
        f"  logs_dir: {tmp_path / 'logs'}\n"
        f"schedule:\n  cron_time: '04:00'\n  timezone: Europe/Moscow\n"
        f"mode:\n  backfill_years: 3\n  default_run_mode: update\n"
        f"discoverer:\n  base_url: https://example.com\n"
        f"  requests_per_second: 1.0\n  request_timeout_s: 30\n"
        f"  max_retries: 3\n  retry_min_wait_s: 0.5\n  retry_max_wait_s: 10\n"
        f"  respect_robots: true\n  user_agent: null\n"
        f"  cookies: {{}}\n  http_backend: httpx\n"
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
        f"  pdf_input_standards: ['IFRS']\n  balance_trim_max_chars: 200000\n"
        f"  vision_fallback_enabled: false\n  vision_fallback_threshold: 0.5\n"
        f"  vision_fallback_max_pages: 8\n  vision_only_global_disabled: false\n"
        f"  vision_only_max_pages_per_request: 25\n"
        f"validator:\n  completeness_threshold: 0.5\n"
        f"orchestrator:\n  publication_concurrency: 4\n"
        f"google_drive:\n  enabled: false\n  folder_id: null\n"
        f"  file_name: e-disclosure.xlsx\n  archive: false\n"
        f"contact_email: null\n",
        encoding="utf-8",
    )
    return cfg


def test_evolve_status_empty(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    cfg = _setup_project(tmp_path)
    rc = main(["--config-dir", str(cfg), "evolve", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no ticks" in out


def test_evolve_status_after_inserting_tick(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    cfg = _setup_project(tmp_path)
    # Bootstrap state by running --version (loads nothing).
    main(["--config-dir", str(cfg), "evolve", "status"])

    # Manually insert one tick into the DB.
    state_db = tmp_path / "data" / "state.sqlite"
    conn = sqlite3.connect(str(state_db))
    conn.execute(
        "INSERT INTO evolution_ticks (started_at, phase, batch_json) "
        "VALUES (?, 'baseline', '[]')",
        ("2026-05-04T10:00:00",),
    )
    conn.commit()
    conn.close()

    rc = main(["--config-dir", str(cfg), "evolve", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "phase=baseline" in out


def test_evolve_reset_unknown_returns_zero(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    cfg = _setup_project(tmp_path)
    rc = main(
        ["--config-dir", str(cfg), "evolve", "reset", "--company-id", "999"]
    )
    assert rc == 0
    assert "no entry" in capsys.readouterr().out


def test_evolve_reset_existing(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    cfg = _setup_project(tmp_path)
    main(["--config-dir", str(cfg), "evolve", "status"])  # ensure DB exists
    state_db = tmp_path / "data" / "state.sqlite"
    conn = sqlite3.connect(str(state_db))
    conn.execute(
        "INSERT INTO evolution_skiplist "
        "(company_id, reason, failure_count, updated_at) "
        "VALUES ('1210', 'manual_blacklist', 0, ?)",
        ("2026-05-04T10:00:00",),
    )
    conn.commit()
    conn.close()

    rc = main(
        ["--config-dir", str(cfg), "evolve", "reset", "--company-id", "1210"]
    )
    assert rc == 0
    assert "removed" in capsys.readouterr().out


def test_evolve_memory_show_missing(monkeypatch, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    cfg = _setup_project(tmp_path)
    monkeypatch.chdir(tmp_path)  # MEMORY.md path is relative
    rc = main(["--config-dir", str(cfg), "evolve", "memory", "show"])
    assert rc == 0
    assert "(missing)" in capsys.readouterr().out


def test_evolve_memory_verify_marks_stale(
    monkeypatch, tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    cfg = _setup_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "evolution").mkdir()
    (tmp_path / "evolution" / "MEMORY.md").write_text(
        "## Patches log\n\n"
        "### evolve(7) — 2026-05-03 — fake\n"
        "- **Files touched:** src/edx/no_such_file.py\n",
        encoding="utf-8",
    )
    rc = main(["--config-dir", str(cfg), "evolve", "memory", "verify"])
    assert rc == 0
    assert "stale references" in capsys.readouterr().out
