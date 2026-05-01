"""ReplicatorService unit tests with a fake CloudStorageProvider."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from edx.providers.storage import RemoteFileInfo
from edx.stages.writer.replicator import ReplicatorService
from edx.storage import Database, RunsRepo


@dataclass
class _FakeProvider:
    name: str = "fake_drive"
    calls: list[tuple[Path, str, str, bool]] = field(default_factory=list)
    response: RemoteFileInfo = field(
        default_factory=lambda: RemoteFileInfo(
            file_id="file-1",
            web_view_link="https://drive.test/view",
            updated_at="2026-05-01T00:00:00+00:00",
        )
    )

    def upsert_file(
        self,
        local_path: Path,
        remote_folder_id: str,
        remote_name: str,
        *,
        archive: bool,
    ) -> RemoteFileInfo:
        self.calls.append((local_path, remote_folder_id, remote_name, archive))
        return self.response


def _local_file(tmp_path: Path) -> Path:
    f = tmp_path / "e-disclosure.xlsx"
    f.write_bytes(b"PK")
    return f


@pytest.fixture
def db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    return db


def test_disabled_skips_provider(tmp_path: Path, db: Database) -> None:
    provider = _FakeProvider()
    with closing(db.connect()) as conn:
        runs_repo = RunsRepo(db, conn)
        service = ReplicatorService(
            provider=provider,
            runs_repo=runs_repo,
            enabled=False,
            folder_id="folder-1",
            file_name="e-disclosure.xlsx",
            archive=False,
        )
        outcome = service.run(_local_file(tmp_path), run_id=None)
    assert outcome.skipped is True
    assert provider.calls == []


def test_no_folder_id_skips(tmp_path: Path, db: Database) -> None:
    provider = _FakeProvider()
    with closing(db.connect()) as conn:
        service = ReplicatorService(
            provider=provider,
            runs_repo=RunsRepo(db, conn),
            enabled=True,
            folder_id=None,
            file_name="e-disclosure.xlsx",
            archive=False,
        )
        outcome = service.run(_local_file(tmp_path))
    assert outcome.skipped is True
    assert provider.calls == []


def test_missing_local_file_skips(tmp_path: Path, db: Database) -> None:
    provider = _FakeProvider()
    with closing(db.connect()) as conn:
        service = ReplicatorService(
            provider=provider,
            runs_repo=RunsRepo(db, conn),
            enabled=True,
            folder_id="folder-1",
            file_name="e-disclosure.xlsx",
            archive=False,
        )
        outcome = service.run(tmp_path / "no-such-file.xlsx")
    assert outcome.skipped is True
    assert provider.calls == []


def test_success_calls_provider_and_updates_runs_row(
    tmp_path: Path, db: Database
) -> None:
    provider = _FakeProvider()
    with closing(db.connect()) as conn:
        runs_repo = RunsRepo(db, conn)
        run_id = runs_repo.start_run(mode="update")
        service = ReplicatorService(
            provider=provider,
            runs_repo=runs_repo,
            enabled=True,
            folder_id="folder-1",
            file_name="e-disclosure.xlsx",
            archive=True,
        )
        outcome = service.run(_local_file(tmp_path), run_id=run_id)
        run_after = runs_repo.get_by_id(run_id)
    assert outcome.skipped is False
    assert outcome.info is not None
    assert outcome.info.file_id == "file-1"
    assert provider.calls == [
        (_local_file(tmp_path), "folder-1", "e-disclosure.xlsx", True)
    ]
    assert run_after is not None
    assert run_after.excel_drive_file_id == "file-1"
    assert run_after.excel_drive_link == "https://drive.test/view"
