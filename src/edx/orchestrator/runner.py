"""Pipeline orchestrator: runs every stage end-to-end for one ``runs`` row.

Resilience model (ТЗ §14):
- Per-publication errors are caught **inside each stage** — the stage flips
  the publication's status to ``failed`` and continues with siblings.
- The orchestrator catches *batch-stage* failures (Discoverer / Writer)
  and surfaces them as ``runs.status = 'failed'`` while still attempting to
  ship the Excel mart with whatever is in the DB.
- Per-publication failures bubble up to ``runs.status = 'partial'``.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from edx.config import TickerEntry
from edx.logging_setup import get_logger
from edx.storage import (
    EventsRepo,
    MetricsRepo,
    PublicationRow,
    PublicationsRepo,
    QAIssuesRepo,
    RunsRepo,
)
from edx.storage.models import PublicationStatus

RunMode = Literal["update", "full_reload"]

EXTRACTED_STATUS: Final[PublicationStatus] = "extracted"


class _AsyncStage(Protocol):
    async def run(
        self, publications: Iterable[PublicationRow], /
    ) -> object: ...


class _SyncStage(Protocol):
    def run(
        self, publications: Iterable[PublicationRow], /
    ) -> object: ...


@dataclass
class StageBundle:
    """Pluggable stages — every Orchestrator test injects mocks here."""

    discoverer: object  # async run(tickers, since)
    downloader: object  # async run(publications)
    unpacker: object  # sync run(publications)
    classifier: object  # sync run(publications)
    text_extractor: object  # sync run(publications)
    metric_extractor: object  # async run(publications)
    event_extractor: object  # async run(publications)
    validator: object  # sync run(publications)
    writer: object  # sync run() -> Path
    replicator: object  # sync run(path, run_id=...)


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    status: str
    stats: dict[str, Any]
    error_summary: str | None


class Orchestrator:
    def __init__(
        self,
        *,
        runs_repo: RunsRepo,
        publications_repo: PublicationsRepo,
        metrics_repo: MetricsRepo,
        events_repo: EventsRepo,
        qa_issues_repo: QAIssuesRepo,
        stages: StageBundle,
        ticker_entries: list[TickerEntry],
        excel_path: Path,
        backfill_years: int = 3,
        ticker_filter: set[str] | None = None,
    ) -> None:
        self.runs_repo = runs_repo
        self.publications_repo = publications_repo
        self.metrics_repo = metrics_repo
        self.events_repo = events_repo
        self.qa_issues_repo = qa_issues_repo
        self.stages = stages
        self.ticker_entries = ticker_entries
        self.excel_path = excel_path
        self.backfill_years = backfill_years
        self.ticker_filter = ticker_filter
        self._log = get_logger("edx.orchestrator")

    async def run(self, mode: RunMode) -> RunOutcome:
        started_at = time.monotonic()
        run_id = self.runs_repo.start_run(mode)
        final_status: Literal["succeeded", "failed", "partial"] = "succeeded"
        error_summary: str | None = None
        stage_errors: list[str] = []
        new_pubs_count = 0

        if mode == "full_reload":
            cutoff = self._backfill_cutoff()
            reset_count = self.publications_repo.reset_status_to_discovered_since(cutoff)
            self._log.info(
                "full_reload_reset",
                cutoff_date=cutoff,
                reset_count=reset_count,
            )

        # --- Discoverer (batch) ---
        try:
            target_tickers = self._select_tickers()
            since_map = {
                t.ticker: self.publications_repo.latest_publication_date(t.ticker)
                for t in target_tickers
            }
            discoverer_result = await self.stages.discoverer.run(  # type: ignore[attr-defined]
                target_tickers, since_map
            )
            new_pubs_count = (
                len(discoverer_result) if discoverer_result is not None else 0
            )
        except Exception as exc:  # noqa: BLE001 — batch failure noted, run continues
            self._log.exception(
                "orchestrator_discoverer_failed",
                error=str(exc),
            )
            final_status = "failed"
            stage_errors.append(f"discoverer: {exc}")

        # --- Per-publication stages ---
        if final_status != "failed":
            await self._run_per_publication_stages(stage_errors)

        # --- Writer (batch) ---
        excel_written: bool = False
        try:
            self.stages.writer.run()  # type: ignore[attr-defined]
            excel_written = True
        except Exception as exc:  # noqa: BLE001
            self._log.exception(
                "orchestrator_writer_failed",
                error=str(exc),
            )
            final_status = "failed"
            stage_errors.append(f"writer: {exc}")

        # --- Replicator (batch, best-effort) ---
        if excel_written:
            try:
                self.stages.replicator.run(  # type: ignore[attr-defined]
                    self.excel_path, run_id=run_id
                )
            except Exception as exc:  # noqa: BLE001
                self._log.exception(
                    "orchestrator_replicator_failed",
                    error=str(exc),
                )
                stage_errors.append(f"replicator: {exc}")

        # Promote partial when at least one publication ended in 'failed' and
        # surface those failures into qa_issues for the QA report (ТЗ §11).
        publications_after = self.publications_repo.list_all()
        self._record_failure_issues(publications_after)
        by_status = self._count_by_status(publications_after)
        if final_status == "succeeded" and by_status.get("failed", 0) > 0:
            final_status = "partial"

        if stage_errors:
            error_summary = "; ".join(stage_errors)

        duration_seconds = round(time.monotonic() - started_at, 2)
        stats = self._build_stats(
            mode=mode,
            duration_seconds=duration_seconds,
            new_publications=new_pubs_count,
            by_status=by_status,
            total_publications=len(publications_after),
        )
        self.runs_repo.finish_run(
            run_id,
            status=final_status,
            stats=stats,
            error_summary=error_summary,
        )
        self._log.info(
            "orchestrator_run_finished",
            run_id=run_id,
            mode=mode,
            status=final_status,
            duration_seconds=duration_seconds,
            stats=stats,
        )
        return RunOutcome(
            run_id=run_id,
            status=final_status,
            stats=stats,
            error_summary=error_summary,
        )

    # ------------------------------------------------------------------
    # Per-publication stages
    # ------------------------------------------------------------------

    async def _run_per_publication_stages(self, errors: list[str]) -> None:
        # Downloader
        await self._run_async_stage_for_status(
            "downloader",
            "discovered",
            self.stages.downloader,
            errors,
        )
        # Unpacker
        self._run_sync_stage_for_status(
            "unpacker",
            "downloaded",
            self.stages.unpacker,
            errors,
        )
        # Classifier
        self._run_sync_stage_for_status(
            "classifier",
            "unpacked",
            self.stages.classifier,
            errors,
        )
        # Text Extractor
        self._run_sync_stage_for_status(
            "text_extractor",
            "classified",
            self.stages.text_extractor,
            errors,
        )
        # Unblock publications that were stuck in 'failed' due to a
        # transient LLM HTTP 402 (no credits).  These should be retried
        # when credits are restored; leaving them as 'failed' would lock
        # them out permanently since the orchestrator never retries that
        # status.
        self.publications_repo.reset_llm_unavailable_to_extracted()
        # Metric Extractor — reports only.
        report_targets = self._filter(
            self.publications_repo.list_by_status(EXTRACTED_STATUS),
            publication_type="report",
        )
        if report_targets:
            await self._run_async_stage(
                "metric_extractor",
                self.stages.metric_extractor,
                report_targets,
                errors,
            )
        # Event Extractor — events only.
        event_targets = self._filter(
            self.publications_repo.list_by_status(EXTRACTED_STATUS),
            publication_type="event",
        )
        if event_targets:
            await self._run_async_stage(
                "event_extractor",
                self.stages.event_extractor,
                event_targets,
                errors,
            )
        # Validator — reports still at 'extracted' (events already validated).
        validator_targets = self._filter(
            self.publications_repo.list_by_status(EXTRACTED_STATUS),
            publication_type="report",
        )
        if validator_targets:
            self._run_sync_stage(
                "validator",
                self.stages.validator,
                validator_targets,
                errors,
            )

    async def _run_async_stage_for_status(
        self,
        name: str,
        status: PublicationStatus,
        stage: object,
        errors: list[str],
    ) -> None:
        targets = self._filter(self.publications_repo.list_by_status(status))
        if not targets:
            return
        await self._run_async_stage(name, stage, targets, errors)

    def _run_sync_stage_for_status(
        self,
        name: str,
        status: PublicationStatus,
        stage: object,
        errors: list[str],
    ) -> None:
        targets = self._filter(self.publications_repo.list_by_status(status))
        if not targets:
            return
        self._run_sync_stage(name, stage, targets, errors)

    async def _run_async_stage(
        self,
        name: str,
        stage: object,
        targets: list[PublicationRow],
        errors: list[str],
    ) -> None:
        try:
            await stage.run(targets)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self._log.exception(
                "orchestrator_stage_failed",
                stage=name,
                error=str(exc),
            )
            errors.append(f"{name}: {exc}")

    def _run_sync_stage(
        self,
        name: str,
        stage: object,
        targets: list[PublicationRow],
        errors: list[str],
    ) -> None:
        try:
            stage.run(targets)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self._log.exception(
                "orchestrator_stage_failed",
                stage=name,
                error=str(exc),
            )
            errors.append(f"{name}: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_tickers(self) -> list[TickerEntry]:
        if not self.ticker_filter:
            return list(self.ticker_entries)
        return [t for t in self.ticker_entries if t.ticker in self.ticker_filter]

    def _filter(
        self,
        publications: list[PublicationRow],
        *,
        publication_type: str | None = None,
    ) -> list[PublicationRow]:
        out = publications
        if self.ticker_filter:
            out = [p for p in out if p.ticker in self.ticker_filter]
        if publication_type is not None:
            out = [p for p in out if p.publication_type == publication_type]
        return out

    def _backfill_cutoff(self) -> str:
        today = datetime.now(UTC).date()
        return (today - timedelta(days=365 * self.backfill_years)).isoformat()

    def _record_failure_issues(
        self, publications: list[PublicationRow]
    ) -> None:
        """Surface every ``failed`` publication as a ``publication_failed``
        qa_issue so downstream Excel/QA reports include them.
        """
        for pub in publications:
            if pub.status != "failed":
                continue
            message = pub.last_error or "publication failed (no detail recorded)"
            self.qa_issues_repo.replace_for_publication(
                pub.publication_id,
                pub.ticker,
                [("publication_failed", message)],
            )

    def _count_by_status(
        self, publications: Iterable[PublicationRow]
    ) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for pub in publications:
            counter[pub.status] += 1
            if pub.is_incomplete:
                counter["incomplete"] += 1
        return dict(counter)

    def _build_stats(
        self,
        *,
        mode: RunMode,
        duration_seconds: float,
        new_publications: int,
        by_status: dict[str, int],
        total_publications: int,
    ) -> dict[str, Any]:
        metrics_count = self.publications_repo.conn.execute(
            "SELECT COUNT(*) AS c FROM metrics"
        ).fetchone()["c"]
        events_count = self.publications_repo.conn.execute(
            "SELECT COUNT(*) AS c FROM events"
        ).fetchone()["c"]
        return {
            "mode": mode,
            "duration_seconds": duration_seconds,
            "new_publications": new_publications,
            "publications_total": total_publications,
            "publications_by_status": by_status,
            "metrics_rows": int(metrics_count),
            "events_rows": int(events_count),
        }
