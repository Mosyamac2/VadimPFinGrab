"""Command-line entry point for the e-disclosure extractor.

The pipeline stages themselves land in subsequent prompts. The CLI is
responsible for parsing arguments, loading + validating configuration,
preparing the state database (migrations + ticker sync), recording a row in
the ``runs`` journal, and dispatching to the eventual orchestrator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import yaml

from edx import __version__
from edx.config import AppSettings, ConfigLoadError, load_all
from edx.http.client import EDisclosureClient, build_user_agent
from edx.logging_setup import configure, get_logger
from edx.stages.discoverer import build_discoverer_service
from edx.stages.discoverer.service import compute_since
from edx.stages.downloader import build_downloader_service
from edx.stages.unpacker import build_unpacker_service
from edx.storage import (
    Database,
    DocumentsRepo,
    PublicationsRepo,
    RunsRepo,
    TickersRepo,
)
from edx.storage.models import PublicationStatus, RunMode

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

    discover_p = subparsers.add_parser(
        "discover",
        help="Run only the Discoverer stage (debug / single-stage replay).",
    )
    discover_p.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict discovery to one or more tickers. May be passed multiple "
            "times. Default: all tickers from tickers.yaml."
        ),
    )
    discover_p.set_defaults(func=_cmd_discover)

    download_p = subparsers.add_parser(
        "download",
        help="Download already-discovered publications into data/raw/.",
    )
    download_p.add_argument(
        "--publication-id",
        action="append",
        help="Restrict download to specific publication IDs (repeatable).",
    )
    download_p.set_defaults(func=_cmd_download)

    unpack_p = subparsers.add_parser(
        "unpack",
        help="Unpack downloaded archives and inventory publication contents.",
    )
    unpack_p.add_argument(
        "--publication-id",
        action="append",
        help="Restrict unpacking to specific publication IDs (repeatable).",
    )
    unpack_p.set_defaults(func=_cmd_unpack)

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


def _cmd_discover(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    db = Database(settings.app.paths.state_db)
    db.migrate()

    with closing(db.connect()) as conn:
        TickersRepo(db, conn).upsert_from_config(settings.tickers.tickers)
        publications_repo = PublicationsRepo(db, conn)

        ticker_filter: set[str] | None = (
            set(args.ticker) if args.ticker else None
        )
        target_tickers = [
            t
            for t in settings.tickers.tickers
            if ticker_filter is None or t.ticker in ticker_filter
        ]
        if ticker_filter and not target_tickers:
            log.error(
                "discover_no_matching_tickers", requested=sorted(ticker_filter)
            )
            return EXIT_RUNTIME_ERROR

        since = compute_since(publications_repo, target_tickers)

        async def _run() -> int:
            service, client = build_discoverer_service(
                settings, publications_repo
            )
            try:
                new_pubs = await service.run(target_tickers, since)
            finally:
                await client.close()
            log.info(
                "discover_finished",
                tickers=[t.ticker for t in target_tickers],
                new_publications=len(new_pubs),
            )
            return EXIT_OK

        return asyncio.run(_run())


def _select_publications(
    publications_repo: PublicationsRepo,
    *,
    status: PublicationStatus,
    publication_ids: list[str] | None,
) -> list:  # type: ignore[type-arg]
    rows = publications_repo.list_by_status(status)
    if publication_ids is None:
        return rows
    requested = set(publication_ids)
    return [r for r in rows if r.publication_id in requested]


def _cmd_download(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        targets = _select_publications(
            publications_repo,
            status="discovered",
            publication_ids=args.publication_id,
        )
        if not targets:
            log.info("download_no_pending_publications")
            return EXIT_OK

        async def _run() -> int:
            async with EDisclosureClient(
                base_url=settings.app.discoverer.base_url,
                user_agent=build_user_agent(settings),
                requests_per_second=settings.app.discoverer.requests_per_second,
                request_timeout_s=settings.app.discoverer.request_timeout_s,
                max_retries=settings.app.discoverer.max_retries,
                retry_min_wait_s=settings.app.discoverer.retry_min_wait_s,
                retry_max_wait_s=settings.app.discoverer.retry_max_wait_s,
                respect_robots=settings.app.discoverer.respect_robots,
            ) as client:
                service = build_downloader_service(
                    settings, publications_repo, client=client
                )
                outcomes = await service.run(targets)
            log.info(
                "download_finished",
                publications=len(targets),
                downloaded=sum(1 for o in outcomes if not o.skipped),
                skipped=sum(1 for o in outcomes if o.skipped),
            )
            return EXIT_OK

        return asyncio.run(_run())


def _cmd_unpack(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        targets = _select_publications(
            publications_repo,
            status="downloaded",
            publication_ids=args.publication_id,
        )
        if not targets:
            log.info("unpack_no_pending_publications")
            return EXIT_OK
        service = build_unpacker_service(
            settings, db, publications_repo, documents_repo
        )
        outcomes = service.run(targets)
        log.info(
            "unpack_finished",
            publications=len(targets),
            archives_extracted=sum(o.archives_extracted for o in outcomes),
            documents_added=sum(o.documents_added for o in outcomes),
        )
    return EXIT_OK


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
