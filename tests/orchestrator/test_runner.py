"""Orchestrator with mocked stages. Single shared connection across mocks/orchestrator."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from edx.config import TickerEntry
from edx.orchestrator import Orchestrator, StageBundle
from edx.storage import (
    Database,
    EventsRepo,
    MetricsRepo,
    PublicationRow,
    PublicationsRepo,
    RunsRepo,
    TickersRepo,
)


@dataclass
class _MockDiscoverer:
    publications_repo: PublicationsRepo
    new_publications: list[tuple[str, str, str]] = field(default_factory=list)
    raise_on_run: Exception | None = None

    async def run(
        self, tickers: Iterable[TickerEntry], since_map: dict[str, str | None]
    ) -> list[object]:
        if self.raise_on_run is not None:
            raise self.raise_on_run
        new = []
        for pub_id, ticker, pub_type in self.new_publications:
            self.publications_repo.upsert_discovered(
                publication_id=pub_id,
                ticker=ticker,
                publication_type=pub_type,  # type: ignore[arg-type]
                publication_date="2026-04-01",
                source_url=f"https://example.test/{pub_id}",
            )
            new.append(pub_id)
        return new


def _transitioner(
    publications_repo: PublicationsRepo,
    *,
    target_status: str,
    fail_for: set[str] | None = None,
) -> Callable[[Iterable[PublicationRow]], None]:
    fail = fail_for or set()

    def _do(publications: Iterable[PublicationRow]) -> None:
        for pub in publications:
            if pub.publication_id in fail:
                publications_repo.mark_status(
                    pub.publication_id, "failed", error="mock failure"
                )
            else:
                publications_repo.mark_status(
                    pub.publication_id, target_status  # type: ignore[arg-type]
                )

    return _do


@dataclass
class _SyncStage:
    transition: Callable[[Iterable[PublicationRow]], None]

    def run(self, publications: Iterable[PublicationRow]) -> None:
        self.transition(publications)


@dataclass
class _AsyncStage:
    transition: Callable[[Iterable[PublicationRow]], None]

    async def run(self, publications: Iterable[PublicationRow]) -> None:
        self.transition(publications)


@dataclass
class _Writer:
    calls: int = 0

    def run(self) -> Path:
        self.calls += 1
        return Path("/tmp/fake.xlsx")


@dataclass
class _Replicator:
    calls: list[tuple[Path, int | None]] = field(default_factory=list)

    def run(self, path: Path, *, run_id: int | None = None) -> None:
        self.calls.append((path, run_id))


def _metric_transition(
    publications_repo: PublicationsRepo,
    publications: Iterable[PublicationRow],
    *,
    fail_for: set[str] | None,
) -> None:
    fail_for = fail_for or set()
    for pub in publications:
        if pub.publication_id in fail_for:
            publications_repo.mark_status(
                pub.publication_id, "failed", error="mock metric extract failure"
            )


@dataclass
class _Workspace:
    """Bag of repos + connection that mocks and orchestrator share."""

    db: Database
    conn: sqlite3.Connection
    publications_repo: PublicationsRepo
    metrics_repo: MetricsRepo
    events_repo: EventsRepo
    runs_repo: RunsRepo


@pytest.fixture
def workspace(tmp_path: Path) -> _Workspace:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    conn = db.connect()
    publications_repo = PublicationsRepo(db, conn)
    metrics_repo = MetricsRepo(db, conn)
    events_repo = EventsRepo(db, conn)
    runs_repo = RunsRepo(db, conn)
    TickersRepo(db, conn).upsert_from_config(
        [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
    )
    return _Workspace(
        db=db,
        conn=conn,
        publications_repo=publications_repo,
        metrics_repo=metrics_repo,
        events_repo=events_repo,
        runs_repo=runs_repo,
    )


def _build_bundle(
    workspace: _Workspace,
    *,
    discoverer: _MockDiscoverer,
    metric_extract_fail: set[str] | None = None,
    writer: _Writer | None = None,
    replicator: _Replicator | None = None,
) -> StageBundle:
    pubs = workspace.publications_repo
    return StageBundle(
        discoverer=discoverer,
        downloader=_AsyncStage(
            transition=_transitioner(pubs, target_status="downloaded")
        ),
        unpacker=_SyncStage(
            transition=_transitioner(pubs, target_status="unpacked")
        ),
        classifier=_SyncStage(
            transition=_transitioner(pubs, target_status="classified")
        ),
        text_extractor=_SyncStage(
            transition=_transitioner(pubs, target_status="extracted")
        ),
        metric_extractor=_AsyncStage(
            transition=lambda items: _metric_transition(
                pubs, items, fail_for=metric_extract_fail
            )
        ),
        event_extractor=_AsyncStage(
            transition=_transitioner(pubs, target_status="validated")
        ),
        validator=_SyncStage(
            transition=_transitioner(pubs, target_status="validated")
        ),
        writer=writer or _Writer(),
        replicator=replicator or _Replicator(),
    )


def _build_orchestrator(
    workspace: _Workspace,
    *,
    bundle: StageBundle,
    backfill_years: int = 3,
) -> Orchestrator:
    return Orchestrator(
        runs_repo=workspace.runs_repo,
        publications_repo=workspace.publications_repo,
        metrics_repo=workspace.metrics_repo,
        events_repo=workspace.events_repo,
        stages=bundle,
        ticker_entries=[
            TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")
        ],
        excel_path=Path("/tmp/fake.xlsx"),
        backfill_years=backfill_years,
    )


@pytest.mark.asyncio
async def test_happy_path_three_publications_reach_validated(
    workspace: _Workspace,
) -> None:
    discoverer = _MockDiscoverer(
        publications_repo=workspace.publications_repo,
        new_publications=[
            ("rep-1", "SBER", "report"),
            ("rep-2", "SBER", "report"),
            ("ev-1", "SBER", "event"),
        ],
    )
    writer = _Writer()
    replicator = _Replicator()
    bundle = _build_bundle(
        workspace,
        discoverer=discoverer,
        writer=writer,
        replicator=replicator,
    )
    orchestrator = _build_orchestrator(workspace, bundle=bundle)
    try:
        outcome = await orchestrator.run("update")
        publications = workspace.publications_repo.list_all()
        runs = workspace.runs_repo.latest(limit=1)
    finally:
        workspace.conn.close()

    assert outcome.status == "succeeded"
    statuses = {p.publication_id: p.status for p in publications}
    assert statuses == {
        "rep-1": "validated",
        "rep-2": "validated",
        "ev-1": "validated",
    }
    assert writer.calls == 1
    assert len(replicator.calls) == 1
    assert runs[0].stats_json is not None
    stats = json.loads(runs[0].stats_json)
    assert stats["publications_total"] == 3
    assert stats["new_publications"] == 3


@pytest.mark.asyncio
async def test_per_publication_failure_yields_partial(
    workspace: _Workspace,
) -> None:
    discoverer = _MockDiscoverer(
        publications_repo=workspace.publications_repo,
        new_publications=[
            ("rep-1", "SBER", "report"),
            ("rep-2", "SBER", "report"),
        ],
    )
    bundle = _build_bundle(
        workspace, discoverer=discoverer, metric_extract_fail={"rep-2"}
    )
    orchestrator = _build_orchestrator(workspace, bundle=bundle)
    try:
        outcome = await orchestrator.run("update")
        publications = workspace.publications_repo.list_all()
    finally:
        workspace.conn.close()

    assert outcome.status == "partial"
    statuses = {p.publication_id: p.status for p in publications}
    assert statuses["rep-1"] == "validated"
    assert statuses["rep-2"] == "failed"


@pytest.mark.asyncio
async def test_discoverer_failure_marks_run_failed_writer_still_runs(
    workspace: _Workspace,
) -> None:
    discoverer = _MockDiscoverer(
        publications_repo=workspace.publications_repo,
        raise_on_run=RuntimeError("discoverer kaboom"),
    )
    writer = _Writer()
    bundle = _build_bundle(workspace, discoverer=discoverer, writer=writer)
    orchestrator = _build_orchestrator(workspace, bundle=bundle)
    try:
        outcome = await orchestrator.run("update")
    finally:
        workspace.conn.close()

    assert outcome.status == "failed"
    assert outcome.error_summary is not None
    assert "discoverer" in outcome.error_summary
    # Writer still runs to flush whatever was already in the DB.
    assert writer.calls == 1


@pytest.mark.asyncio
async def test_full_reload_resets_only_publications_in_horizon(
    workspace: _Workspace,
) -> None:
    today = datetime.now(UTC).date()
    recent = (today - timedelta(days=30)).isoformat()
    old = (today - timedelta(days=365 * 5)).isoformat()
    pubs = workspace.publications_repo
    pubs.upsert_discovered(
        publication_id="recent",
        ticker="SBER",
        publication_type="report",
        publication_date=recent,
        source_url="https://x",
    )
    pubs.mark_status("recent", "written")
    pubs.upsert_discovered(
        publication_id="old",
        ticker="SBER",
        publication_type="report",
        publication_date=old,
        source_url="https://y",
    )
    pubs.mark_status("old", "written")

    discoverer = _MockDiscoverer(
        publications_repo=pubs, new_publications=[]
    )
    bundle = _build_bundle(workspace, discoverer=discoverer)
    orchestrator = _build_orchestrator(workspace, bundle=bundle)
    try:
        await orchestrator.run("full_reload")
        statuses = {p.publication_id: p.status for p in pubs.list_all()}
    finally:
        workspace.conn.close()

    # Recent reset to discovered → through stages → validated.
    assert statuses["recent"] == "validated"
    # Old publication outside the 3y window stays untouched.
    assert statuses["old"] == "written"
