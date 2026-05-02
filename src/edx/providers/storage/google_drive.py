"""Google Drive cloud-storage provider.

Authentication is non-interactive at runtime: the caller provides a
refresh-token-based ``Credentials`` object built from values stored in
``.env``. The interactive OAuth flow (``InstalledAppFlow``) lives in the
``edx auth google-drive`` CLI command and is run once by the operator.

Patch 24 — proxy support
------------------------
The Drive API client lives on top of ``httplib2``, which (unlike
``httpx``) **does not** read ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``NO_PROXY``
environment variables on its own. Operators behind a corporate or
country-blocking proxy (e.g. RU VPS reaching Google through a vless
tunnel on ``127.0.0.1:10809``) need an explicit proxy hand-off into the
``httplib2.Http`` instance under the API client.

We delegate per-request proxy resolution to
``httplib2.proxy_info_from_environment`` — passed as the ``proxy_info``
*callable* (not a one-shot value) so httplib2 invokes it with the
target scheme on every request, picking up ``HTTPS_PROXY`` for
``https://...`` URLs and ``HTTP_PROXY`` for ``http://...``, and
honouring ``NO_PROXY`` as a bypass list. When no proxy env var is set
at construction time the helper returns ``None`` and the provider
falls back to the original ``credentials=`` path so deployments
without a proxy keep working unchanged.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httplib2  # type: ignore[import-untyped]
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import (  # type: ignore[import-untyped]
    AuthorizedHttp,
)
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from edx.logging_setup import get_logger
from edx.providers.storage.base import (
    GoogleDriveCredentialsMissingError,
    RemoteFileInfo,
)

DRIVE_SCOPES: Final[tuple[str, ...]] = (
    "https://www.googleapis.com/auth/drive.file",
)
EXCEL_MIME: Final[str] = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
FOLDER_MIME: Final[str] = "application/vnd.google-apps.folder"
ARCHIVE_SUBFOLDER_NAME: Final[str] = "archive"


class GoogleDriveProvider:
    """Polite Drive client with the upsert-by-name convention from ТЗ §10.4."""

    name = "google_drive"

    def __init__(self, *, service: Any) -> None:
        # ``service`` is a ``googleapiclient.discovery.Resource`` instance —
        # left untyped because googleapiclient is not type-stubbed.
        self.service = service
        self._log = get_logger("edx.providers.storage.google_drive")

    @classmethod
    def create(
        cls,
        *,
        client_id: str | None,
        client_secret: str | None,
        refresh_token: str | None,
    ) -> GoogleDriveProvider:
        if not (client_id and client_secret and refresh_token):
            raise GoogleDriveCredentialsMissingError(
                "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / "
                "GOOGLE_OAUTH_REFRESH_TOKEN must be set in .env"
            )
        creds = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            scopes=list(DRIVE_SCOPES),
        )
        # Patch 24: route Drive traffic through the env proxy when set so
        # operators behind a vless/SOCKS tunnel (RU VPS reaching Google via
        # a local CONNECT proxy) work without code changes per-deploy.
        # ``httplib2.proxy_info_from_environment`` parses HTTPS_PROXY /
        # HTTP_PROXY / NO_PROXY and returns ``None`` when they're empty.
        authed_http = _build_authorized_http_with_env_proxy(creds)
        if authed_http is not None:
            service = build(
                "drive", "v3", http=authed_http, cache_discovery=False
            )
        else:
            service = build(
                "drive", "v3", credentials=creds, cache_discovery=False
            )
        return cls(service=service)

    def upsert_file(
        self,
        local_path: Path,
        remote_folder_id: str,
        remote_name: str,
        *,
        archive: bool,
    ) -> RemoteFileInfo:
        existing_id = self._find_in_folder(remote_folder_id, remote_name)
        if existing_id is not None:
            file_id, link = self._update_existing(existing_id, local_path)
            self._log.info(
                "drive_file_updated",
                file_id=file_id,
                folder_id=remote_folder_id,
                name=remote_name,
            )
        else:
            file_id, link = self._create_in_folder(
                local_path, remote_folder_id, remote_name
            )
            self._log.info(
                "drive_file_created",
                file_id=file_id,
                folder_id=remote_folder_id,
                name=remote_name,
            )

        if archive:
            archive_folder_id = self._ensure_subfolder(
                remote_folder_id, ARCHIVE_SUBFOLDER_NAME
            )
            archive_name = self._archive_name(remote_name)
            archived_id, _ = self._create_in_folder(
                local_path, archive_folder_id, archive_name
            )
            self._log.info(
                "drive_archive_snapshot_created",
                file_id=archived_id,
                folder_id=archive_folder_id,
                name=archive_name,
            )

        return RemoteFileInfo(
            file_id=file_id,
            web_view_link=link,
            updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    # ----------------------------- helpers -----------------------------

    def _find_in_folder(
        self, folder_id: str, name: str
    ) -> str | None:
        safe_name = name.replace("'", r"\'")
        query = (
            f"name = '{safe_name}' and '{folder_id}' in parents "
            f"and trashed = false"
        )
        response = (
            self.service.files()
            .list(q=query, fields="files(id,name)", pageSize=10)
            .execute()
        )
        files = response.get("files") or []
        if not files:
            return None
        return str(files[0]["id"])

    def _create_in_folder(
        self, local_path: Path, parent_id: str, name: str
    ) -> tuple[str, str]:
        media = MediaFileUpload(
            str(local_path), mimetype=EXCEL_MIME, resumable=False
        )
        body = {"name": name, "parents": [parent_id]}
        response = (
            self.service.files()
            .create(
                body=body,
                media_body=media,
                fields="id,webViewLink",
            )
            .execute()
        )
        return str(response["id"]), str(response.get("webViewLink") or "")

    def _update_existing(
        self, file_id: str, local_path: Path
    ) -> tuple[str, str]:
        media = MediaFileUpload(
            str(local_path), mimetype=EXCEL_MIME, resumable=False
        )
        response = (
            self.service.files()
            .update(
                fileId=file_id,
                media_body=media,
                fields="id,webViewLink",
            )
            .execute()
        )
        return str(response["id"]), str(response.get("webViewLink") or "")

    def _ensure_subfolder(self, parent_id: str, name: str) -> str:
        query = (
            f"name = '{name}' and mimeType = '{FOLDER_MIME}' "
            f"and '{parent_id}' in parents and trashed = false"
        )
        response = (
            self.service.files()
            .list(q=query, fields="files(id,name)", pageSize=10)
            .execute()
        )
        files = response.get("files") or []
        if files:
            return str(files[0]["id"])
        body = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }
        created = (
            self.service.files().create(body=body, fields="id").execute()
        )
        return str(created["id"])

    @staticmethod
    def _archive_name(remote_name: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
        path = Path(remote_name)
        return f"{path.stem}-{timestamp}{path.suffix}"


_PROXY_ENV_VAR_NAMES: Final[tuple[str, ...]] = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def _build_authorized_http_with_env_proxy(creds: Credentials) -> Any | None:
    """Return an ``AuthorizedHttp`` honouring HTTPS_PROXY/HTTP_PROXY/NO_PROXY.

    Returns ``None`` when no proxy env vars are set — the caller falls
    back to the default ``build(credentials=...)`` path, which avoids
    constructing a needless ``httplib2.Http`` for the common case.

    Note: ``httplib2.proxy_info_from_environment`` is passed **as a
    callable** to ``Http(proxy_info=...)``. httplib2 calls it lazily
    per request with the target scheme (``"https"`` / ``"http"``),
    which is the only shape that lets the same client honour
    ``HTTPS_PROXY`` for HTTPS URLs and ``HTTP_PROXY`` for plain HTTP
    on the same connection pool, plus ``NO_PROXY`` as a bypass list.

    Extracted so a unit test can probe the wiring without going through
    the real ``googleapiclient.discovery.build`` (which would try to
    fetch the discovery document over the network).
    """
    proxy_var = next(
        (n for n in _PROXY_ENV_VAR_NAMES if os.environ.get(n)),
        None,
    )
    if proxy_var is None:
        return None
    log = get_logger("edx.providers.storage.google_drive")
    log.info(
        "drive_proxy_configured",
        env_var=proxy_var,
        proxy_url=os.environ[proxy_var],
        no_proxy=os.environ.get("NO_PROXY")
        or os.environ.get("no_proxy")
        or "",
    )
    http = httplib2.Http(proxy_info=httplib2.proxy_info_from_environment)
    return AuthorizedHttp(creds, http=http)
