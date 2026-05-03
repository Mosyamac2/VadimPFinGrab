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
import re
import time
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import yaml

from edx import __version__
from edx.config import AppSettings, ConfigLoadError, load_all
from edx.http import build_http_client
from edx.logging_setup import configure, get_logger
from edx.orchestrator import Orchestrator, StageBundle
from edx.providers.llm import LLMUnavailableError, build_llm_provider
from edx.stages.classifier import build_classifier_service
from edx.stages.discoverer import build_discoverer_service
from edx.stages.discoverer.service import compute_since
from edx.stages.downloader import build_downloader_service
from edx.stages.event_extractor import build_event_extractor_service
from edx.stages.metric_extractor import build_metric_extractor_service
from edx.stages.text_extractor import build_text_extractor_service
from edx.stages.unpacker import build_unpacker_service
from edx.stages.validator import build_validator_service
from edx.stages.writer import build_replicator_service, build_writer_service
from edx.storage import (
    Database,
    DocumentsRepo,
    EventsRepo,
    MetricsRepo,
    PublicationsRepo,
    QAIssuesRepo,
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
    run_p.add_argument(
        "--ticker",
        action="append",
        help="Restrict the pipeline to specific tickers (repeatable).",
    )
    run_p.set_defaults(func=_cmd_run)

    status_p = subparsers.add_parser(
        "status",
        help="Print a summary of the latest pipeline runs.",
    )
    status_p.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of recent runs to show (default: 5).",
    )
    status_p.set_defaults(func=_cmd_status)

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

    classify_p = subparsers.add_parser(
        "classify",
        help="Classify unpacked PDFs (reporting standard, form, machine-readability).",
    )
    classify_p.add_argument(
        "--publication-id",
        action="append",
        help="Restrict classification to specific publication IDs (repeatable).",
    )
    classify_p.set_defaults(func=_cmd_classify)

    extract_p = subparsers.add_parser(
        "extract-text",
        help="Extract plain text (and tables) from classified PDFs.",
    )
    extract_p.add_argument(
        "--publication-id",
        action="append",
        help="Restrict extraction to specific publication IDs (repeatable).",
    )
    extract_p.set_defaults(func=_cmd_extract_text)

    extract_metrics_p = subparsers.add_parser(
        "extract-metrics",
        help="Run the LLM Metric Extractor over publications with status='extracted'.",
    )
    extract_metrics_p.add_argument(
        "--publication-id",
        action="append",
        help="Restrict extraction to specific publication IDs (repeatable).",
    )
    extract_metrics_p.set_defaults(func=_cmd_extract_metrics)

    extract_events_p = subparsers.add_parser(
        "extract-events",
        help="Run the LLM Event Extractor over publications with publication_type='event'.",
    )
    extract_events_p.add_argument(
        "--publication-id",
        action="append",
        help="Restrict extraction to specific publication IDs (repeatable).",
    )
    extract_events_p.set_defaults(func=_cmd_extract_events)

    validate_p = subparsers.add_parser(
        "validate",
        help="Run sanity checks over extracted metrics; produce qa_issues report.",
    )
    validate_p.add_argument(
        "--publication-id",
        action="append",
        help="Restrict validation to specific publication IDs (repeatable).",
    )
    validate_p.set_defaults(func=_cmd_validate)

    export_p = subparsers.add_parser(
        "export-excel",
        help="Generate the Excel mart from state.sqlite (full snapshot).",
    )
    export_p.set_defaults(func=_cmd_export_excel)

    replicate_p = subparsers.add_parser(
        "replicate",
        help="Upload the local Excel mart to Google Drive (config.google_drive).",
    )
    replicate_p.set_defaults(func=_cmd_replicate)

    auth_p = subparsers.add_parser(
        "auth",
        help="Interactive authentication helpers.",
    )
    auth_sub = auth_p.add_subparsers(
        dest="auth_command", required=True, metavar="subcommand"
    )
    auth_drive_p = auth_sub.add_parser(
        "google-drive",
        help="Run the OAuth flow once and print the refresh token.",
    )
    auth_drive_p.set_defaults(func=_cmd_auth_google_drive)

    cache_p = subparsers.add_parser(
        "cache",
        help="Manage on-disk caches.",
    )
    cache_sub = cache_p.add_subparsers(
        dest="cache_command", required=True, metavar="subcommand"
    )
    cache_prune_p = cache_sub.add_parser(
        "prune",
        help="Remove LLM cache entries older than the given duration.",
    )
    cache_prune_p.add_argument(
        "--older-than",
        type=_parse_duration,
        default=30 * 86400,
        help="Drop entries older than DURATION (e.g. 30d, 12h, 45m). Default: 30d.",
    )
    cache_prune_p.set_defaults(func=_cmd_cache_prune)

    # Patch 40-43: self-evolution loop.
    evolve_p = subparsers.add_parser(
        "evolve",
        help="Self-evolution loop subcommands (Patch 38+).",
    )
    evolve_sub = evolve_p.add_subparsers(
        dest="evolve_command", required=True, metavar="subcommand"
    )

    evolve_tick_p = evolve_sub.add_parser(
        "tick", help="Run one self-evolution tick over a batch of 3 companies."
    )
    evolve_tick_p.set_defaults(func=_cmd_evolve_tick)

    evolve_status_p = evolve_sub.add_parser(
        "status", help="Show recent ticks from evolution_ticks."
    )
    evolve_status_p.add_argument("--limit", type=int, default=10)
    evolve_status_p.set_defaults(func=_cmd_evolve_status)

    evolve_report_p = evolve_sub.add_parser(
        "report",
        help="Aggregate stats over the last 7 days (verdicts, cost, skiplist).",
    )
    evolve_report_p.set_defaults(func=_cmd_evolve_report)

    evolve_reset_p = evolve_sub.add_parser(
        "reset", help="Remove a company from the skiplist."
    )
    evolve_reset_p.add_argument("--company-id", required=True)
    evolve_reset_p.set_defaults(func=_cmd_evolve_reset)

    evolve_canary_p = evolve_sub.add_parser(
        "canary", help="Canary baseline subcommands."
    )
    evolve_canary_sub = evolve_canary_p.add_subparsers(
        dest="canary_command", required=True, metavar="subcommand"
    )
    evolve_canary_capture_p = evolve_canary_sub.add_parser(
        "capture",
        help="Snapshot SBER/LKOH/IZNM and write data/canary_baseline.json.",
    )
    evolve_canary_capture_p.set_defaults(func=_cmd_evolve_canary_capture)

    evolve_memory_p = evolve_sub.add_parser(
        "memory", help="Inspect or maintain evolution/MEMORY.md."
    )
    evolve_memory_sub = evolve_memory_p.add_subparsers(
        dest="memory_command", required=True, metavar="subcommand"
    )
    evolve_memory_show_p = evolve_memory_sub.add_parser(
        "show", help="Print evolution/MEMORY.md to stdout."
    )
    evolve_memory_show_p.set_defaults(func=_cmd_evolve_memory_show)
    evolve_memory_verify_p = evolve_memory_sub.add_parser(
        "verify",
        help="Cross-check MEMORY.md against the working tree (stale entries).",
    )
    evolve_memory_verify_p.set_defaults(func=_cmd_evolve_memory_verify)

    return parser


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_DURATION_MULTIPLIER = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def _parse_duration(value: str) -> int:
    """Parse strings like ``30d``, ``12h`` into seconds."""
    match = _DURATION_RE.match(value)
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid duration {value!r} — expected like 30d, 12h, 45m"
        )
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return amount * _DURATION_MULTIPLIER[unit]


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


def _execute_pipeline_run(
    settings: AppSettings,
    mode: RunMode,
    *,
    ticker_filter: set[str] | None = None,
) -> int:
    """Drive the full pipeline DAG via :class:`Orchestrator`."""
    log = get_logger("edx.cli")
    db = Database(settings.app.paths.state_db)
    db.migrate()

    try:
        llm_provider = build_llm_provider(settings)
    except LLMUnavailableError as exc:
        log.error("llm_unavailable", error=str(exc))
        return EXIT_RUNTIME_ERROR

    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        metrics_repo = MetricsRepo(db, conn)
        events_repo = EventsRepo(db, conn)
        qa_issues_repo = QAIssuesRepo(db, conn)
        runs_repo = RunsRepo(db, conn)
        tickers_repo = TickersRepo(db, conn)
        tickers_repo.upsert_from_config(settings.tickers.tickers)

        async def _run_pipeline() -> int:
            async with build_http_client(settings) as http_client:
                from edx.stages.discoverer import build_discoverer_service

                discoverer, _ = build_discoverer_service(
                    settings, publications_repo, client=http_client
                )
                downloader = build_downloader_service(
                    settings, publications_repo, client=http_client
                )
                unpacker = build_unpacker_service(
                    settings, db, publications_repo, documents_repo
                )
                classifier = build_classifier_service(
                    settings, publications_repo, documents_repo
                )
                text_extractor = build_text_extractor_service(
                    settings, publications_repo, documents_repo
                )
                metric_extractor = build_metric_extractor_service(
                    settings,
                    publications_repo,
                    documents_repo,
                    metrics_repo,
                    llm_provider,
                )
                event_extractor = build_event_extractor_service(
                    settings,
                    publications_repo,
                    documents_repo,
                    events_repo,
                    llm_provider,
                )
                validator = build_validator_service(
                    settings, publications_repo, metrics_repo, qa_issues_repo
                )
                writer = build_writer_service(
                    settings,
                    publications_repo,
                    metrics_repo,
                    events_repo,
                    qa_issues_repo,
                    tickers_repo,
                )
                replicator = build_replicator_service(settings, runs_repo)

                bundle = StageBundle(
                    discoverer=discoverer,
                    downloader=downloader,
                    unpacker=unpacker,
                    classifier=classifier,
                    text_extractor=text_extractor,
                    metric_extractor=metric_extractor,
                    event_extractor=event_extractor,
                    validator=validator,
                    writer=writer,
                    replicator=replicator,
                )
                orchestrator = Orchestrator(
                    runs_repo=runs_repo,
                    publications_repo=publications_repo,
                    metrics_repo=metrics_repo,
                    events_repo=events_repo,
                    qa_issues_repo=qa_issues_repo,
                    stages=bundle,
                    ticker_entries=settings.tickers.tickers,
                    excel_path=settings.app.paths.excel_path,
                    backfill_years=settings.app.mode.backfill_years,
                    ticker_filter=ticker_filter,
                )
                outcome = await orchestrator.run(mode)
                log.info(
                    "cli_command_invoked",
                    command=mode,
                    run_id=outcome.run_id,
                    status=outcome.status,
                )
                if outcome.status == "failed":
                    return EXIT_RUNTIME_ERROR
                return EXIT_OK

        return asyncio.run(_run_pipeline())


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
    ticker_filter = set(args.ticker) if args.ticker else None
    return _execute_pipeline_run(
        settings_or_code, mode=mode, ticker_filter=ticker_filter
    )


def _cmd_status(args: argparse.Namespace) -> int:
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        runs = RunsRepo(db, conn).latest(limit=args.limit)
    if not runs:
        print("(no runs recorded yet)")
        return EXIT_OK
    for run in runs:
        stats = json.loads(run.stats_json) if run.stats_json else {}
        print(
            f"#{run.run_id} {run.mode:11s} {run.status:10s} "
            f"started={run.started_at} duration={stats.get('duration_seconds', '?')}s"
        )
        if stats:
            by_status = stats.get("publications_by_status", {})
            print(
                f"    publications={stats.get('publications_total', 0)} "
                f"by_status={by_status} "
                f"metrics={stats.get('metrics_rows', 0)} "
                f"events={stats.get('events_rows', 0)}"
            )
        if run.excel_drive_link:
            print(f"    drive: {run.excel_drive_link}")
        if run.error_summary:
            print(f"    error: {run.error_summary}")
    return EXIT_OK


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
            async with build_http_client(settings) as client:
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


def _cmd_classify(args: argparse.Namespace) -> int:
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
            status="unpacked",
            publication_ids=args.publication_id,
        )
        if not targets:
            log.info("classify_no_pending_publications")
            return EXIT_OK
        service = build_classifier_service(
            settings, publications_repo, documents_repo
        )
        outcomes = service.run(targets)
        log.info(
            "classify_finished",
            publications=len(targets),
            classified=len(outcomes),
            machine_readable=sum(o.machine_readable_count for o in outcomes),
            scans=sum(o.scan_count for o in outcomes),
        )
    return EXIT_OK


def _cmd_extract_text(args: argparse.Namespace) -> int:
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
            status="classified",
            publication_ids=args.publication_id,
        )
        if not targets:
            log.info("extract_text_no_pending_publications")
            return EXIT_OK
        service = build_text_extractor_service(
            settings, publications_repo, documents_repo
        )
        outcomes = service.run(targets)
        log.info(
            "extract_text_finished",
            publications=len(targets),
            extracted=len(outcomes),
            documents=sum(o.documents_processed for o in outcomes),
            native=sum(o.native_count for o in outcomes),
            ocr=sum(o.ocr_count for o in outcomes),
        )
    return EXIT_OK


def _cmd_extract_metrics(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    try:
        llm_provider = build_llm_provider(settings)
    except LLMUnavailableError as exc:
        log.error("llm_unavailable", error=str(exc))
        return EXIT_RUNTIME_ERROR

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        metrics_repo = MetricsRepo(db, conn)

        targets = _select_publications(
            publications_repo,
            status="extracted",
            publication_ids=args.publication_id,
        )
        report_targets = [p for p in targets if p.publication_type == "report"]
        if not report_targets:
            log.info("extract_metrics_no_pending_publications")
            return EXIT_OK

        service = build_metric_extractor_service(
            settings,
            publications_repo,
            documents_repo,
            metrics_repo,
            llm_provider,
        )

        async def _run() -> int:
            outcomes = await service.run(report_targets)
            log.info(
                "extract_metrics_finished",
                publications=len(report_targets),
                processed=len(outcomes),
                rows_written=sum(o.rows_written for o in outcomes),
                incomplete=sum(1 for o in outcomes if o.is_incomplete),
            )
            return EXIT_OK

        return asyncio.run(_run())


def _cmd_extract_events(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    try:
        llm_provider = build_llm_provider(settings)
    except LLMUnavailableError as exc:
        log.error("llm_unavailable", error=str(exc))
        return EXIT_RUNTIME_ERROR

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        documents_repo = DocumentsRepo(db, conn)
        events_repo = EventsRepo(db, conn)

        targets = _select_publications(
            publications_repo,
            status="extracted",
            publication_ids=args.publication_id,
        )
        event_targets = [p for p in targets if p.publication_type == "event"]
        if not event_targets:
            log.info("extract_events_no_pending_publications")
            return EXIT_OK

        service = build_event_extractor_service(
            settings,
            publications_repo,
            documents_repo,
            events_repo,
            llm_provider,
        )

        async def _run() -> int:
            outcomes = await service.run(event_targets)
            log.info(
                "extract_events_finished",
                publications=len(event_targets),
                processed=len(outcomes),
                fallback_event_type=sum(
                    1 for o in outcomes if o.used_fallback_event_type
                ),
                fallback_event_date=sum(
                    1 for o in outcomes if o.used_fallback_event_date
                ),
                summary_truncated=sum(
                    1 for o in outcomes if o.summary_truncated
                ),
            )
            return EXIT_OK

        return asyncio.run(_run())


def _cmd_validate(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        publications_repo = PublicationsRepo(db, conn)
        metrics_repo = MetricsRepo(db, conn)
        qa_issues_repo = QAIssuesRepo(db, conn)
        targets = _select_publications(
            publications_repo,
            status="extracted",
            publication_ids=args.publication_id,
        )
        report_targets = [p for p in targets if p.publication_type == "report"]
        if not report_targets:
            log.info("validate_no_pending_publications")
            return EXIT_OK
        service = build_validator_service(
            settings, publications_repo, metrics_repo, qa_issues_repo
        )
        outcomes = service.run(report_targets)
        log.info(
            "validate_finished",
            publications=len(report_targets),
            processed=len(outcomes),
            warnings_total=sum(o.warnings_count for o in outcomes),
            incomplete=sum(1 for o in outcomes if o.is_incomplete),
        )
    return EXIT_OK


def _cmd_export_excel(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        service = build_writer_service(
            settings,
            PublicationsRepo(db, conn),
            MetricsRepo(db, conn),
            EventsRepo(db, conn),
            QAIssuesRepo(db, conn),
            TickersRepo(db, conn),
        )
        path = service.run()
        log.info("export_excel_finished", path=str(path))

        # Chain to replicator if configured.
        replicator = build_replicator_service(settings, RunsRepo(db, conn))
        outcome = replicator.run(path)
        if not outcome.skipped and outcome.info is not None:
            log.info(
                "export_excel_replicated",
                file_id=outcome.info.file_id,
                link=outcome.info.web_view_link,
            )
    return EXIT_OK


def _cmd_replicate(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        replicator = build_replicator_service(settings, RunsRepo(db, conn))
        outcome = replicator.run(settings.app.paths.excel_path)
    if outcome.skipped:
        log.warning("replicate_skipped", reason=outcome.reason)
        return EXIT_OK
    log.info(
        "replicate_finished",
        file_id=outcome.info.file_id if outcome.info else None,
        link=outcome.info.web_view_link if outcome.info else None,
    )
    return EXIT_OK


def _cmd_auth_google_drive(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    client_id_secret = settings.secrets.google_oauth_client_id
    client_secret_secret = settings.secrets.google_oauth_client_secret
    if client_id_secret is None or client_secret_secret is None:
        log.error(
            "oauth_missing_credentials",
            message=(
                "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET "
                "in .env first."
            ),
        )
        return EXIT_RUNTIME_ERROR

    # Imported lazily so test environments without google_auth_oauthlib
    # binaries don't fail at parser-build time.
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id_secret.get_secret_value(),
                "client_secret": client_secret_secret.get_secret_value(),
                "redirect_uris": ["http://localhost"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    creds = flow.run_local_server(port=0)
    refresh_token = getattr(creds, "refresh_token", None)
    if not refresh_token:
        log.error(
            "oauth_no_refresh_token",
            message=(
                "Google did not return a refresh token. Re-run after "
                "revoking previous app access at "
                "https://myaccount.google.com/permissions."
            ),
        )
        return EXIT_RUNTIME_ERROR
    print(refresh_token)
    log.info(
        "oauth_drive_refresh_token_obtained",
        message=(
            "Paste the value above into .env as GOOGLE_OAUTH_REFRESH_TOKEN, "
            "then re-run `edx replicate`."
        ),
    )
    return EXIT_OK


def _cmd_evolve_tick(args: argparse.Namespace) -> int:
    """Run one self-evolution tick."""
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    from edx.evolve.tick import run_one_tick

    run_one_tick(settings_or_code)
    return EXIT_OK


def _cmd_evolve_status(args: argparse.Namespace) -> int:
    """Print the last N evolve ticks as a compact table."""
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    from edx.storage import Database, EvolutionRepo

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        repo = EvolutionRepo(db, conn)
        ticks = repo.latest_ticks(limit=int(args.limit))
    if not ticks:
        print("(no ticks recorded yet)")
        return EXIT_OK
    for tick in ticks:
        print(
            f"#{tick.tick_id:5d} "
            f"started={tick.started_at} "
            f"phase={tick.phase} "
            f"verdict={tick.verdict or '—'} "
            f"cost=${(tick.claude_cost_usd or 0):.3f} "
            f"turns={tick.claude_turns or 0} "
            f"sha={tick.commit_sha or '—'}"
        )
        if tick.error_summary:
            print(f"        error: {tick.error_summary}")
    return EXIT_OK


def _cmd_evolve_report(args: argparse.Namespace) -> int:
    """Aggregates over the last 7 days."""
    from datetime import UTC, datetime, timedelta

    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    from edx.storage import Database, EvolutionRepo

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        repo = EvolutionRepo(db, conn)
        # Reuse latest_ticks for a generous window; trim by date below.
        ticks = repo.latest_ticks(limit=2000)
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    window = [t for t in ticks if t.started_at >= cutoff]
    by_verdict: dict[str, int] = {}
    cost_total = 0.0
    cost_ok_total = 0.0
    cost_ok_count = 0
    for t in window:
        v = t.verdict or "running"
        by_verdict[v] = by_verdict.get(v, 0) + 1
        c = t.claude_cost_usd or 0.0
        cost_total += c
        if t.verdict == "ok":
            cost_ok_total += c
            cost_ok_count += 1

    print(f"Evolve report — last 7 days ({len(window)} ticks)")
    for verdict, count in sorted(by_verdict.items()):
        print(f"  {verdict:20s} {count}")
    print(f"  cost total:  ${cost_total:.2f}")
    if cost_ok_count:
        print(f"  cost / ok:   ${cost_ok_total / cost_ok_count:.3f}")

    with closing(db.connect()) as conn:
        skip = EvolutionRepo(db, conn).get_skiplist()
    print(f"Skiplist size: {len(skip)}")
    by_reason: dict[str, int] = {}
    for entry in skip:
        by_reason[entry.reason] = by_reason.get(entry.reason, 0) + 1
    for reason, count in sorted(by_reason.items()):
        print(f"  {reason:20s} {count}")
    return EXIT_OK


def _cmd_evolve_reset(args: argparse.Namespace) -> int:
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code
    from edx.storage import Database, EvolutionRepo

    db = Database(settings.app.paths.state_db)
    db.migrate()
    with closing(db.connect()) as conn:
        repo = EvolutionRepo(db, conn)
        ok = repo.reset(args.company_id)
    print("removed" if ok else "no entry to remove")
    return EXIT_OK


def _cmd_evolve_canary_capture(args: argparse.Namespace) -> int:
    """Snapshot canaries to data/canary_baseline.json."""
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    from edx.evolve import canaries
    from edx.storage import Database

    db = Database(settings.app.paths.state_db)
    db.migrate()
    target = canaries.canary_baseline_path(settings.app.paths.state_db)
    with closing(db.connect()) as conn:
        snaps = canaries.take_canary_baseline(conn, target)
    print(f"Wrote {target}")
    for ticker, snap in snaps.items():
        print(
            f"  {ticker:6s} publications={snap.publications_total} "
            f"metrics={snap.metrics_rows} "
            f"written={snap.publications_by_status.get('written', 0)}"
        )
    return EXIT_OK


def _cmd_evolve_memory_show(args: argparse.Namespace) -> int:
    from edx.evolve import memory as memory_module

    if not memory_module.MEMORY_PATH.exists():
        print(f"(missing) {memory_module.MEMORY_PATH}")
        return EXIT_OK
    print(memory_module.MEMORY_PATH.read_text(encoding="utf-8"))
    return EXIT_OK


def _cmd_evolve_memory_verify(args: argparse.Namespace) -> int:
    """Best-effort sanity check of MEMORY.md against the working tree.

    For each ``- **Files touched:** path/foo, path/bar`` line, verify
    that the listed paths still exist. Print stale references so the
    operator can run ``edx evolve memory compact`` if needed.
    """
    import re

    from edx.evolve import memory as memory_module

    if not memory_module.MEMORY_PATH.exists():
        print(f"(missing) {memory_module.MEMORY_PATH}")
        return EXIT_OK
    text = memory_module.MEMORY_PATH.read_text(encoding="utf-8")
    line_re = re.compile(
        r"^\s*-\s+\*\*Files touched:\*\*\s*(?P<paths>.+)$",
        re.MULTILINE,
    )
    stale: list[str] = []
    for match in line_re.finditer(text):
        for raw in match.group("paths").split(","):
            path = raw.strip().strip("`").strip()
            if not path:
                continue
            if not Path(path).exists():
                stale.append(path)
    if not stale:
        print("memory ok — all listed paths exist")
    else:
        print(f"stale references ({len(stale)}):")
        for path in stale:
            print(f"  {path}")
    return EXIT_OK


def _cmd_cache_prune(args: argparse.Namespace) -> int:
    log = get_logger("edx.cli")
    settings_or_code = _load_settings_or_exit(args)
    if isinstance(settings_or_code, int):
        return settings_or_code
    settings = settings_or_code

    cache_dir = settings.app.paths.processed_dir / "_llm_cache"
    if not cache_dir.exists():
        log.info(
            "cache_prune_skipped_no_cache_dir", path=str(cache_dir)
        )
        return EXIT_OK

    cutoff = time.time() - float(args.older_than)
    removed = 0
    kept = 0
    for entry in cache_dir.glob("*.json"):
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime < cutoff:
            entry.unlink()
            removed += 1
        else:
            kept += 1

    log.info(
        "cache_prune_done",
        path=str(cache_dir),
        older_than_seconds=args.older_than,
        removed=removed,
        kept=kept,
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
