"""``edx evolve tick`` orchestrator — Patch 40 (no Claude Code yet).

This is the bare scaffolding: pick batch → synth config → snapshot →
run pipeline → snapshot → record verdict. On any failure the bundle
directory exists but Claude Code is NOT invoked yet (Patch 42).

The function is deliberately resilient: subprocess failures, timeouts
and per-ticker fail verdicts do NOT raise. Only catastrophic errors
(e.g. inability to read CSV) propagate, so the systemd timer doesn't
go red because of a normal failed tick.
"""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml

from edx.config import AppSettings
from edx.evolve import bundle as bundle_module
from edx.evolve.csv_loader import load_companies
from edx.evolve.picker import PickerInput, pick_next_batch
from edx.evolve.runner import run_pipeline_on_batch
from edx.evolve.snapshot import TickerSnapshot, snapshot_batch
from edx.evolve.synth import write_evolve_config
from edx.evolve.verdict import (
    TickerVerdict,
    VerdictCode,
    aggregate_verdict,
    compute_verdict,
)
from edx.logging_setup import get_logger
from edx.storage import Database, EvolutionRepo

DEFAULT_CSV_PATH: Final[Path] = Path("e-disclosure-companies.csv")
DEFAULT_MAIN_TICKERS_YAML: Final[Path] = Path("config/tickers.yaml")
DEFAULT_EVOLVE_CONFIG_DIR: Final[Path] = Path("config-evolve")
DEFAULT_BUNDLE_ROOT: Final[Path] = Path("evolution/runs")
DEFAULT_PIPELINE_TIMEOUT_S: Final[int] = 30 * 60


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_moex_e_disclosure_ids(
    yaml_path: Path = DEFAULT_MAIN_TICKERS_YAML,
) -> frozenset[str]:
    """Extract real e_disclosure_id values from the main tickers.yaml.

    Skips placeholder values (``REPLACE_ME``) and entries with empty
    ``e_disclosure_id``. Missing file or unparseable YAML → empty set.
    """
    if not yaml_path.exists():
        return frozenset()
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    raw_tickers = data.get("tickers") or []
    if not isinstance(raw_tickers, list):
        return frozenset()
    out: set[str] = set()
    for entry in raw_tickers:
        if not isinstance(entry, dict):
            continue
        edx_id = entry.get("e_disclosure_id")
        if not isinstance(edx_id, str):
            continue
        edx_id = edx_id.strip()
        if not edx_id or edx_id.upper() == "REPLACE_ME":
            continue
        out.add(edx_id)
    return frozenset(out)


def run_one_tick(
    settings: AppSettings,
    *,
    csv_path: Path = DEFAULT_CSV_PATH,
    main_tickers_yaml: Path = DEFAULT_MAIN_TICKERS_YAML,
    evolve_config_dir: Path = DEFAULT_EVOLVE_CONFIG_DIR,
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
    cooldown_days: int = 7,
    batch_size: int = 3,
    pipeline_timeout_s: int = DEFAULT_PIPELINE_TIMEOUT_S,
) -> int:
    """Run one self-evolve tick. Returns ``tick_id`` or ``0`` if no batch.

    This is the Patch 40 version — Claude Code is not wired in yet.
    On any FAIL/REGRESSION the bundle directory simply exists for
    later post-mortem; Patch 42 will pick it up and run the agent.
    """
    log = get_logger("edx.evolve.tick")

    companies = load_companies(csv_path)
    moex_ids = read_moex_e_disclosure_ids(main_tickers_yaml)

    db = Database(settings.app.paths.state_db)
    db.migrate()

    with closing(db.connect()) as conn:
        repo = EvolutionRepo(db, conn)
        batch = pick_next_batch(
            PickerInput(
                companies=companies,
                moex_e_disclosure_ids=moex_ids,
                cooldown_days=cooldown_days,
                batch_size=batch_size,
            ),
            repo,
        )
        if len(batch) < batch_size:
            log.info("evolve_no_candidates", picked=len(batch))
            return 0

        started_at = _utc_now_iso()
        batch_payload = [
            {
                "company_id": c.company_id,
                "name": c.name,
                "ticker": c.synthetic_ticker,
                "profile": c.type,
            }
            for c in batch
        ]
        batch_json = json.dumps(batch_payload, ensure_ascii=False)
        tick_id = repo.create_tick(
            started_at=started_at,
            phase="baseline",
            batch_json=batch_json,
        )
        log.info(
            "evolve_tick_started",
            tick_id=tick_id,
            tickers=[c.synthetic_ticker for c in batch],
        )

        bundle_dir = bundle_root / str(tick_id)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "batch.json").write_text(
            json.dumps(batch_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        write_evolve_config(batch, target_dir=evolve_config_dir)

        synthetic_tickers = [c.synthetic_ticker for c in batch]
        snaps_before = snapshot_batch(conn, synthetic_tickers)
        _save_snapshots(bundle_dir / "snap_before.json", snaps_before)

        log_path = bundle_dir / "pipeline.log"
        run_result = run_pipeline_on_batch(
            tickers=synthetic_tickers,
            config_dir=evolve_config_dir,
            log_path=log_path,
            timeout_seconds=pipeline_timeout_s,
        )
        # The pipeline ran in a subprocess with its own connection;
        # to read its writes we need a fresh view over the same file
        # (sqlite WAL ensures consistency), and we can simply re-issue
        # SELECTs through our open ``conn``.
        snaps_after = snapshot_batch(conn, synthetic_tickers)
        _save_snapshots(bundle_dir / "snap_after.json", snaps_after)

        verdicts: dict[str, TickerVerdict] = {
            t: compute_verdict(
                snaps_before[t],
                snaps_after[t],
                pipeline_returncode=run_result.returncode,
            )
            for t in synthetic_tickers
        }
        overall: VerdictCode = aggregate_verdict(verdicts)

        verdicts_json = json.dumps(
            {t: asdict(v) for t, v in verdicts.items()},
            ensure_ascii=False,
        )
        snaps_before_json = json.dumps(
            {k: v.as_json_dict() for k, v in snaps_before.items()},
            ensure_ascii=False,
        )
        snaps_after_json = json.dumps(
            {k: v.as_json_dict() for k, v in snaps_after.items()},
            ensure_ascii=False,
        )

        # Patch 41: on a non-OK overall, assemble a Diagnostic Bundle so
        # Patch 42's agent has everything it needs. We still don't invoke
        # the agent here — that lands in Patch 42.
        if overall != "ok":
            try:
                bundle_module.assemble(
                    bundle_dir,
                    batch=batch,
                    snaps_before=snaps_before,
                    snaps_after=snaps_after,
                    verdicts=verdicts,
                    log_path=log_path,
                    state_db=settings.app.paths.state_db,
                    conn=conn,
                )
            except Exception as exc:  # noqa: BLE001 — never break the tick
                log.warning("evolve_bundle_assemble_failed", error=str(exc))

        finished_at = _utc_now_iso()
        per_ticker_codes = {t: v.code for t, v in verdicts.items()}
        error_summary = (
            None
            if overall == "ok"
            else f"baseline overall={overall}; per-ticker={per_ticker_codes}"
        )
        # Patch 41: when bundle is built we transition through claude_code
        # and land in failed (Patch 42 will swap the second update_tick
        # for the actual agent call).
        repo.update_tick(
            tick_id,
            phase="done" if overall == "ok" else "failed",
            verdict=overall,
            snaps_before_json=snaps_before_json,
            snaps_after_json=snaps_after_json,
            verdicts_json=verdicts_json,
            bundle_path=str(bundle_dir),
            finished_at=finished_at,
            error_summary=error_summary,
        )

        log.info(
            "evolve_tick_finished",
            tick_id=tick_id,
            verdict=overall,
            duration_seconds=run_result.duration_seconds,
            timed_out=run_result.timed_out,
            returncode=run_result.returncode,
            per_ticker={t: v.code for t, v in verdicts.items()},
        )
        return tick_id


def _save_snapshots(
    path: Path, snaps: dict[str, TickerSnapshot]
) -> None:
    payload = {k: v.as_json_dict() for k, v in snaps.items()}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = ["run_one_tick", "read_moex_e_disclosure_ids"]
