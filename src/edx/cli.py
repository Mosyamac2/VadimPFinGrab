"""Command-line entry point for the e-disclosure extractor.

The pipeline stages themselves land in subsequent prompts. The CLI is
responsible for parsing arguments, loading + validating configuration,
preparing the state database (migrations + ticker sync), recording a row in
the ``runs`` journal, and dispatching to the eventual orchestrator.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import yaml

from edx import __version__
from edx.config import AppSettings, ConfigLoadError, load_all
from edx.logging_setup import configure, get_logger
from edx.storage import Database, RunsRepo, TickersRepo
from edx.storage.models import RunMode

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_RUNTIME_ERROR = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edx",
        description="ETL pipeline for e-disclosure.ru financial reports.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Directory containing the six YAML config files (default: ./config).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    update_p = subparsers.add_parser(
        "update",
        help="Incremental run (the 'refresh' button). Default scheduled mode.",
    )
    update_p.set_defaults(func=_cmd_update)

    run_p = subparsers.add_parser(
        "run",
        help="Run the pipeline with explicit options (e.g. full reload).",
    )
    run_p.add_argument(
        "--full-reload",
        action="store_true",
        help="Re-process the last 3 years of publications.",
    )
    run_p.set_defaults(func=_cmd_run)

    config_p = subparsers.add_parser(
        "config",
        help="Configuration helpers (validate, print).",
    )
    config_sub = config_p.add_subparsers(
        dest="config_command", required=True, metavar="subcommand"
    )

    config_check_p = config_sub.add_parser(
        "check",
        help="Validate config files and print loaded values with secrets masked.",
    )
    config_check_p.add_argument(
        "--format",
        choices=("yaml", "json"),
        default="yaml",
        help="Output format (default: yaml).",
    )
    config_check_p.set_defaults(func=_cmd_config_check)

    return parser


def _load_settings_or_exit(args: argparse.Namespace) -> AppSettings | int:
    log = get_logger("edx.cli")
    try:
        return load_all(args.config_dir)
    except ConfigLoadError as exc:
        log.error(
            "config_load_failed",
            file=str(exc.file_path),
            field=exc.field_path,
            message=exc.message,
        )
        return EXIT_CONFIG_ERROR


def _execute_pipeline_run(settings: AppSettings, mode: RunMode) -> int:
    """Run the (currently empty) pipeline scaffolding for the given mode.

    Real stages plug in here in subsequent prompts. For now: migrate the
    state database, sync tickers from YAML, open + close a row in ``runs``.
    """
    log = get_logger("edx.cli")
    db = Database(settings.app.paths.state_db)
    applied_versions = db.migrate()

    with closing(db.connect()) as conn:
        tickers_repo = TickersRepo(db, conn)
        runs_repo = RunsRepo(db, conn)

        ticker_count = tickers_repo.upsert_from_config(settings.tickers.tickers)
        log.info(
            "tickers_synced",
            ticker_count=ticker_count,
            applied_migrations=applied_versions,
        )

        run_id = runs_repo.start_run(mode)
        log.info(
            "cli_command_invoked",
            command=mode,
            run_id=run_id,
            ticker_count=ticker_count,
        )

        try:
            stats = {
                "ticker_count": ticker_count,
                "migrations_applied": applied_versions,
                "publications_total": 0,
            }
            runs_repo.finish_run(run_id, status="succeeded", stats=stats)
            log.info("run_finished", run_id=run_id, status="succeeded")
        except Exception as exc:
            runs_repo.finish_run(
                run_id,
                status="failed",
                error_summary=f"{type(exc).__name__}: {exc}",
            )
            log.error("run_finished", run_id=run_id, status="failed", error=str(exc))
            return EXIT_RUNTIME_ERROR

    return EXIT_OK


def _cmd_update(args: argparse.Namespace) -> int:
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    return _execute_pipeline_run(settings_or_code, mode="update")


def _cmd_run(args: argparse.Namespace) -> int:
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    mode: RunMode = "full_reload" if args.full_reload else "update"
    return _execute_pipeline_run(settings_or_code, mode=mode)


def _cmd_config_check(args: argparse.Namespace) -> int:
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    masked = settings_or_code.to_masked_dict()
    if args.format == "json":
        print(json.dumps(masked, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            yaml.safe_dump(
                masked,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    configure()
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
