"""GoogleDriveProvider against a mocked googleapiclient service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from edx.providers.storage.base import GoogleDriveCredentialsMissingError
from edx.providers.storage.google_drive import (
    ARCHIVE_SUBFOLDER_NAME,
    GoogleDriveProvider,
)


def _make_service(
    list_responses: list[dict],
    create_responses: list[dict],
    update_responses: list[dict],
) -> MagicMock:
    service = MagicMock()
    service.files.return_value.list.return_value.execute.side_effect = list_responses
    service.files.return_value.create.return_value.execute.side_effect = create_responses
    service.files.return_value.update.return_value.execute.side_effect = update_responses
    return service


def _local_file(tmp_path: Path) -> Path:
    f = tmp_path / "e-disclosure.xlsx"
    f.write_bytes(b"PK")
    return f


def test_first_upload_calls_create(tmp_path: Path) -> None:
    service = _make_service(
        list_responses=[{"files": []}],
        create_responses=[
            {"id": "drive-file-id", "webViewLink": "https://drive.test/view"}
        ],
        update_responses=[],
    )
    provider = GoogleDriveProvider(service=service)
    info = provider.upsert_file(
        _local_file(tmp_path),
        remote_folder_id="folder-1",
        remote_name="e-disclosure.xlsx",
        archive=False,
    )
    assert info.file_id == "drive-file-id"
    assert info.web_view_link == "https://drive.test/view"
    # files.list called once for the lookup.
    assert service.files.return_value.list.return_value.execute.call_count == 1
    # files.create called once for the main file.
    assert (
        service.files.return_value.create.return_value.execute.call_count == 1
    )


def test_second_upload_calls_update(tmp_path: Path) -> None:
    service = _make_service(
        list_responses=[{"files": [{"id": "existing-id", "name": "x"}]}],
        create_responses=[],
        update_responses=[
            {"id": "existing-id", "webViewLink": "https://drive.test/same"}
        ],
    )
    provider = GoogleDriveProvider(service=service)
    info = provider.upsert_file(
        _local_file(tmp_path),
        remote_folder_id="folder-1",
        remote_name="e-disclosure.xlsx",
        archive=False,
    )
    assert info.file_id == "existing-id"
    assert info.web_view_link == "https://drive.test/same"
    # No create — only update.
    assert (
        service.files.return_value.create.return_value.execute.call_count == 0
    )
    assert (
        service.files.return_value.update.return_value.execute.call_count == 1
    )


def test_archive_creates_subfolder_then_dated_copy(tmp_path: Path) -> None:
    service = _make_service(
        list_responses=[
            {"files": []},  # main file lookup → not present
            {"files": []},  # archive subfolder lookup → not present
        ],
        create_responses=[
            {
                "id": "main-id",
                "webViewLink": "https://drive.test/main",
            },  # create main
            {"id": "archive-folder-id"},  # create archive subfolder
            {
                "id": "archive-snapshot-id",
                "webViewLink": "https://drive.test/snap",
            },  # create dated snapshot
        ],
        update_responses=[],
    )
    provider = GoogleDriveProvider(service=service)
    info = provider.upsert_file(
        _local_file(tmp_path),
        remote_folder_id="folder-1",
        remote_name="e-disclosure.xlsx",
        archive=True,
    )
    assert info.file_id == "main-id"
    create_calls = service.files.return_value.create.return_value.execute.call_count
    list_calls = service.files.return_value.list.return_value.execute.call_count
    assert create_calls == 3  # main + archive folder + dated snapshot
    assert list_calls == 2  # main lookup + archive subfolder lookup


def test_archive_subfolder_reused_when_present(tmp_path: Path) -> None:
    service = _make_service(
        list_responses=[
            {"files": []},  # main file lookup → not present
            {  # archive subfolder lookup → ALREADY EXISTS
                "files": [{"id": "archive-folder-id", "name": ARCHIVE_SUBFOLDER_NAME}]
            },
        ],
        create_responses=[
            {"id": "main-id", "webViewLink": "https://drive.test/main"},
            {"id": "snapshot-id", "webViewLink": "https://drive.test/snap"},
        ],
        update_responses=[],
    )
    provider = GoogleDriveProvider(service=service)
    provider.upsert_file(
        _local_file(tmp_path),
        remote_folder_id="folder-1",
        remote_name="e-disclosure.xlsx",
        archive=True,
    )
    # 2 creates only: main + dated snapshot. Subfolder is reused.
    assert (
        service.files.return_value.create.return_value.execute.call_count == 2
    )


def test_create_raises_when_credentials_missing() -> None:
    with pytest.raises(GoogleDriveCredentialsMissingError):
        GoogleDriveProvider.create(
            client_id="abc", client_secret=None, refresh_token="r"
        )


def test_archive_name_uses_iso_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dated snapshot embeds a YYYY-MM-DD-HHMM stamp."""

    from datetime import UTC, datetime

    import edx.providers.storage.google_drive as drive_module

    fixed = datetime(2026, 5, 1, 12, 30, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> _FixedDatetime:  # type: ignore[override]
            return _FixedDatetime(2026, 5, 1, 12, 30, tzinfo=UTC)

    monkeypatch.setattr(drive_module, "datetime", _FixedDatetime)

    service = _make_service(
        list_responses=[{"files": []}, {"files": []}],
        create_responses=[
            {"id": "main", "webViewLink": "x"},
            {"id": "archive-folder"},
            {"id": "snapshot", "webViewLink": "y"},
        ],
        update_responses=[],
    )
    provider = GoogleDriveProvider(service=service)
    provider.upsert_file(
        _local_file(tmp_path),
        remote_folder_id="folder-1",
        remote_name="e-disclosure.xlsx",
        archive=True,
    )
    snapshot_call = service.files.return_value.create.call_args_list[2]
    body = snapshot_call.kwargs["body"]
    assert body["name"] == "e-disclosure-2026-05-01-1230.xlsx"
    assert fixed.isoformat()  # silence unused warning
