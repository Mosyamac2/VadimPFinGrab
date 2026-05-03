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
import os
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from edx.config import AppSettings
from edx.evolve import bundle as bundle_module
from edx.evolve import canaries as canaries_module
from edx.evolve import claude_runner as claude_runner_module
from edx.evolve import git_ops as git_ops_module
from edx.evolve import memory as memory_module
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

DEFAULT_TICK_BUDGET_USD: Final[float] = 100.0
DEFAULT_DAILY_BUDGET_USD: Final[float] = 1000.0
AGENT_ENABLED_ENV: Final[str] = "EDX_EVOLVE_AGENT_ENABLED"
TICK_BUDGET_ENV: Final[str] = "EDX_EVOLVE_TICK_BUDGET_USD"
DAILY_BUDGET_ENV: Final[str] = "EDX_EVOLVE_DAILY_BUDGET_USD"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _agent_enabled() -> bool:
    return os.environ.get(AGENT_ENABLED_ENV) == "1"


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
        # the agent has everything it needs.
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

        per_ticker_codes = {t: v.code for t, v in verdicts.items()}
        common_update_kwargs: dict[str, Any] = {
            "snaps_before_json": snaps_before_json,
            "snaps_after_json": snaps_after_json,
            "verdicts_json": verdicts_json,
            "bundle_path": str(bundle_dir),
        }

        # Happy path: nothing to fix, close the tick.
        if overall == "ok":
            repo.update_tick(
                tick_id,
                phase="done",
                verdict="ok",
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            log.info(
                "evolve_tick_finished",
                tick_id=tick_id,
                verdict="ok",
                duration_seconds=run_result.duration_seconds,
                returncode=run_result.returncode,
                per_ticker=per_ticker_codes,
            )
            return tick_id

        # Patch 42: on FAIL/REGRESSION optionally invoke Claude Code.
        # The verdict gate (tests / canaries / improvement / commit) is
        # added in Patch 43 — for now we only run the agent and record
        # cost / session metadata so the operator can inspect dry-runs.
        agent_enabled = _agent_enabled()
        daily_cap = _env_float(DAILY_BUDGET_ENV, DEFAULT_DAILY_BUDGET_USD)
        tick_cap = _env_float(TICK_BUDGET_ENV, DEFAULT_TICK_BUDGET_USD)

        if not agent_enabled:
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict=overall,
                error_summary=(
                    f"agent_disabled (set {AGENT_ENABLED_ENV}=1 to invoke); "
                    f"baseline overall={overall}; per-ticker={per_ticker_codes}"
                ),
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            log.info(
                "evolve_tick_finished",
                tick_id=tick_id,
                verdict=overall,
                agent_enabled=False,
                per_ticker=per_ticker_codes,
            )
            return tick_id

        spent_today = repo.daily_cost_usd(date.today().isoformat())
        if spent_today >= daily_cap:
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict="skipped_budget",
                error_summary=(
                    f"daily budget reached: ${spent_today:.2f} / ${daily_cap:.2f}"
                ),
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            log.warning(
                "evolve_tick_skipped_budget",
                tick_id=tick_id,
                spent_today=spent_today,
                daily_cap=daily_cap,
            )
            return tick_id

        repo.update_tick(tick_id, phase="claude_code")
        memory_before = (
            memory_module.MEMORY_PATH.read_text(encoding="utf-8")
            if memory_module.MEMORY_PATH.exists()
            else ""
        )

        # Patch 43: agent runs on a dedicated branch evolve/tick-N so
        # any partial edit can be discarded without touching master.
        try:
            git_ops_module.create_tick_branch(Path("."), tick_id)
        except Exception as exc:  # noqa: BLE001
            log.error("evolve_git_branch_failed", error=str(exc))
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict="fail",
                error_summary=f"git_branch_failed: {exc!r}",
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            return tick_id

        claude_res = claude_runner_module.run_agent(
            bundle_dir=bundle_dir,
            tick_id=tick_id,
            project_root=Path("."),
            budget_usd=tick_cap,
        )

        memory_after = (
            memory_module.MEMORY_PATH.read_text(encoding="utf-8")
            if memory_module.MEMORY_PATH.exists()
            else ""
        )
        memory_updated = memory_module.has_new_entry_since(
            memory_before, memory_after, tick_id
        )

        # ---- Patch 43 verdict gate ----
        gate_failure = _gate_check_after_agent(
            claude_res=claude_res,
            memory_updated=memory_updated,
        )
        if gate_failure is not None:
            git_ops_module.abandon_branch(Path("."), tick_id)
            verdict_code: VerdictCode = "fail"
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict=verdict_code,
                claude_session=claude_res.session_id,
                claude_cost_usd=claude_res.cost_usd,
                claude_turns=claude_res.turns,
                error_summary=gate_failure,
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            _bump_skiplist_for_failing(repo, batch, verdicts, tick_id)
            log.info(
                "evolve_tick_finished",
                tick_id=tick_id,
                verdict=verdict_code,
                agent_error=gate_failure,
                agent_cost=claude_res.cost_usd,
            )
            return tick_id

        # tests gate
        if not _run_make_target(Path("."), "test"):
            git_ops_module.abandon_branch(Path("."), tick_id)
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict="regression_tests",
                claude_session=claude_res.session_id,
                claude_cost_usd=claude_res.cost_usd,
                claude_turns=claude_res.turns,
                error_summary="make_test_red",
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            _bump_skiplist_for_failing(repo, batch, verdicts, tick_id)
            log.info(
                "evolve_tick_finished",
                tick_id=tick_id,
                verdict="regression_tests",
            )
            return tick_id

        # canary gate
        canary_reports = canaries_module.check_canaries(
            conn, canaries_module.canary_baseline_path(settings.app.paths.state_db)
        )
        if not all(c.ok for c in canary_reports):
            git_ops_module.abandon_branch(Path("."), tick_id)
            failing_canaries = [c.ticker for c in canary_reports if not c.ok]
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict="regression_canary",
                claude_session=claude_res.session_id,
                claude_cost_usd=claude_res.cost_usd,
                claude_turns=claude_res.turns,
                error_summary=f"canaries failed: {failing_canaries}",
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            _bump_skiplist_for_failing(repo, batch, verdicts, tick_id)
            log.info(
                "evolve_tick_finished",
                tick_id=tick_id,
                verdict="regression_canary",
            )
            return tick_id

        # batch improvement gate: re-run pipeline on the batch and
        # confirm at least one previously-failing ticker is now ok and
        # nobody regressed.
        retry_log = bundle_dir / "pipeline.log.retry"
        retry_result = run_pipeline_on_batch(
            tickers=synthetic_tickers,
            config_dir=evolve_config_dir,
            log_path=retry_log,
            timeout_seconds=pipeline_timeout_s,
        )
        snaps_retry = snapshot_batch(conn, synthetic_tickers)
        verdicts_retry: dict[str, TickerVerdict] = {
            t: compute_verdict(
                snaps_before[t],
                snaps_retry[t],
                pipeline_returncode=retry_result.returncode,
            )
            for t in synthetic_tickers
        }
        improved, not_regressed = _batch_improvement(verdicts, verdicts_retry)

        if not (improved and not_regressed):
            git_ops_module.abandon_branch(Path("."), tick_id)
            reason = ",".join(
                ([] if improved else ["no_improvement"])
                + ([] if not_regressed else ["regression"])
            )
            verdict_code = "regression" if not not_regressed else "fail"
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict=verdict_code,
                claude_session=claude_res.session_id,
                claude_cost_usd=claude_res.cost_usd,
                claude_turns=claude_res.turns,
                error_summary=reason,
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            _bump_skiplist_for_failing(repo, batch, verdicts_retry, tick_id)
            log.info(
                "evolve_tick_finished",
                tick_id=tick_id,
                verdict=verdict_code,
                reason=reason,
            )
            return tick_id

        # All gates green — auto-merge.
        commit_message = _compose_commit_message(
            tick_id=tick_id,
            batch=batch,
            verdicts=verdicts,
            verdicts_retry=verdicts_retry,
            claude_res=claude_res,
        )
        merge_res = git_ops_module.commit_and_merge(
            Path("."), tick_id, commit_message, push=True
        )
        if not merge_res.pushed or merge_res.commit_sha is None:
            git_ops_module.abandon_branch(Path("."), tick_id)
            repo.update_tick(
                tick_id,
                phase="failed",
                verdict="fail",
                claude_session=claude_res.session_id,
                claude_cost_usd=claude_res.cost_usd,
                claude_turns=claude_res.turns,
                error_summary=f"git_failed: {merge_res.notes}",
                finished_at=_utc_now_iso(),
                **common_update_kwargs,
            )
            log.warning(
                "evolve_tick_git_merge_failed",
                tick_id=tick_id,
                notes=merge_res.notes,
            )
            return tick_id

        repo.update_tick(
            tick_id,
            phase="done",
            verdict="ok",
            claude_session=claude_res.session_id,
            claude_cost_usd=claude_res.cost_usd,
            claude_turns=claude_res.turns,
            commit_sha=merge_res.commit_sha,
            finished_at=_utc_now_iso(),
            **common_update_kwargs,
        )
        log.info(
            "evolve_tick_finished",
            tick_id=tick_id,
            verdict="ok",
            commit_sha=merge_res.commit_sha,
            agent_cost=claude_res.cost_usd,
            agent_turns=claude_res.turns,
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


def _gate_check_after_agent(
    *,
    claude_res: claude_runner_module.ClaudeRunResult,
    memory_updated: bool,
) -> str | None:
    """Return error string when the agent run is rejected pre-tests.

    Returns ``None`` when we should advance to tests/canaries/batch gates.
    """
    if claude_res.is_error:
        return claude_res.error_summary or "claude_run_error"
    if not claude_res.modified_files:
        return "claude_no_changes"
    if not memory_updated:
        return "memory_not_updated"
    return None


def _run_make_target(cwd: Path, target: str) -> bool:
    """Returns True iff `make TARGET` exits with code 0."""
    import subprocess

    proc = subprocess.run(
        ["make", target], cwd=str(cwd), capture_output=True, text=True
    )
    return proc.returncode == 0


def _batch_improvement(
    before_verdicts: dict[str, TickerVerdict],
    after_verdicts: dict[str, TickerVerdict],
) -> tuple[bool, bool]:
    improved = any(
        before_verdicts[t].code in ("fail", "regression")
        and after_verdicts[t].code == "ok"
        for t in before_verdicts
    )
    not_regressed = all(
        after_verdicts[t].code != "regression"
        for t in before_verdicts
    )
    return improved, not_regressed


def _bump_skiplist_for_failing(
    repo: EvolutionRepo,
    batch: list,  # type: ignore[type-arg]
    verdicts: dict[str, TickerVerdict],
    tick_id: int,
) -> None:
    for company in batch:
        ticker = company.synthetic_ticker
        verdict = verdicts.get(ticker)
        if verdict is None or verdict.code == "ok":
            continue
        repo.bump_failure(company.company_id, tick_id)


def _compose_commit_message(
    *,
    tick_id: int,
    batch: list,  # type: ignore[type-arg]
    verdicts: dict[str, TickerVerdict],
    verdicts_retry: dict[str, TickerVerdict],
    claude_res: claude_runner_module.ClaudeRunResult,
) -> str:
    tickers = [c.synthetic_ticker for c in batch]
    improved = [
        t
        for t in tickers
        if verdicts[t].code in ("fail", "regression")
        and verdicts_retry[t].code == "ok"
    ]
    return (
        f"evolve({tick_id}): batch [{','.join(tickers)}]\n"
        f"\n"
        f"companies improved: {improved}\n"
        f"per-ticker before: { {t: verdicts[t].code for t in tickers} }\n"
        f"per-ticker after:  { {t: verdicts_retry[t].code for t in tickers} }\n"
        f"\n"
        f"Claude Code session: {claude_res.session_id}\n"
        f"Cost: ${claude_res.cost_usd:.3f}  Turns: {claude_res.turns}\n"
        f"\n"
        f"Updated evolution/MEMORY.md.\n"
    )


__all__ = ["run_one_tick", "read_moex_e_disclosure_ids"]
