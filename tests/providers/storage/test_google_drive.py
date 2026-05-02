"""GoogleDriveProvider against a mocked googleapiclient service."""

from __future__ import annotations

from pathlib import Path
from unittest import mock
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


# --- Patch 24 — env proxy support -----------------------------------------


def test_no_proxy_returns_none_so_caller_uses_credentials_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without HTTPS_PROXY / HTTP_PROXY env vars, helper returns None and
    the create() flow falls back to ``build(credentials=...)`` — preserves
    behaviour for deployments without a proxy."""
    from edx.providers.storage.google_drive import (
        _build_authorized_http_with_env_proxy,
    )

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)

    creds = mock.MagicMock(name="creds")
    assert _build_authorized_http_with_env_proxy(creds) is None


def test_https_proxy_env_yields_authorized_http_with_proxy_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With HTTPS_PROXY set, helper returns AuthorizedHttp wrapping an
    httplib2.Http whose proxy_info is a *callable* — when invoked with
    the ``https`` scheme it resolves to a ProxyInfo that points at
    127.0.0.1:10809 (the vless tunnel front-end on the operator's VPS)."""
    import httplib2

    from edx.providers.storage.google_drive import (
        _build_authorized_http_with_env_proxy,
    )

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10809")
    monkeypatch.delenv("NO_PROXY", raising=False)

    creds = mock.MagicMock(name="creds")
    authed = _build_authorized_http_with_env_proxy(creds)
    assert authed is not None
    assert isinstance(authed.http, httplib2.Http)
    # We hand httplib2 a callable so it picks the right env var per
    # request scheme. Resolve it manually for the assertion.
    pi_callable = authed.http.proxy_info
    assert callable(pi_callable)
    pi_https = pi_callable("https")
    assert pi_https is not None
    assert pi_https.proxy_host == "127.0.0.1"
    assert pi_https.proxy_port == 10809


def test_only_http_proxy_set_still_yields_authorized_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If only HTTP_PROXY is set (no HTTPS_PROXY), the helper still
    returns the wrapper — httplib2 will then resolve to that proxy for
    plain-http URLs and bypass for https. We don't second-guess the
    operator's choice."""
    import httplib2

    from edx.providers.storage.google_drive import (
        _build_authorized_http_with_env_proxy,
    )

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10809")
    monkeypatch.delenv("NO_PROXY", raising=False)

    creds = mock.MagicMock(name="creds")
    authed = _build_authorized_http_with_env_proxy(creds)
    assert authed is not None
    assert isinstance(authed.http, httplib2.Http)


def test_no_proxy_env_is_passed_through_to_httplib2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NO_PROXY`` configured by the operator must reach httplib2. Since
    we delegate via a callable, httplib2 reads the env vars at the
    moment of each request — no need to hard-code the bypass list. The
    helper itself only logs the configured value; httplib2's
    ``ProxyInfo`` carries its own bypass-host list once instantiated."""
    import httplib2

    from edx.providers.storage.google_drive import (
        _build_authorized_http_with_env_proxy,
    )

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10809")
    monkeypatch.setenv("NO_PROXY", "e-disclosure.ru,localhost")

    creds = mock.MagicMock(name="creds")
    authed = _build_authorized_http_with_env_proxy(creds)
    assert authed is not None
    # The hand-off is the callable itself — proxy_info is bound to
    # httplib2.proxy_info_from_environment so each request reads the
    # then-current env (including NO_PROXY).
    assert authed.http.proxy_info is httplib2.proxy_info_from_environment
    # Sanity check: when invoked for an external scheme, it returns a
    # ProxyInfo carrying the configured bypass list.
    pi = httplib2.proxy_info_from_environment("https")
    assert pi is not None
    bypass = getattr(pi, "bypass_hosts", None)
    # ``bypass_hosts`` is a frozen tuple-like or list of regex patterns
    # depending on httplib2 version; either way 'e-disclosure.ru' must
    # appear in the source list.
    if bypass is not None:
        assert any("e-disclosure.ru" in str(b) for b in bypass)
