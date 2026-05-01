"""ReplicatorService: ship the Excel mart to Google Drive (ТЗ §10.4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from edx.config import AppSettings
from edx.logging_setup import get_logger
from edx.providers.storage import (
    CloudStorageProvider,
    GoogleDriveCredentialsMissingError,
    GoogleDriveProvider,
    RemoteFileInfo,
)
from edx.storage import RunsRepo


@dataclass(frozen=True)
class ReplicateOutcome:
    skipped: bool
    info: RemoteFileInfo | None
    reason: str | None = None


class ReplicatorService:
    """Per-run upload of the Excel mart. No-op when disabled."""

    def __init__(
        self,
        provider: CloudStorageProvider | None,
        runs_repo: RunsRepo,
        *,
        enabled: bool,
        folder_id: str | None,
        file_name: str,
        archive: bool,
    ) -> None:
        self.provider = provider
        self.runs_repo = runs_repo
        self.enabled = enabled
        self.folder_id = folder_id
        self.file_name = file_name
        self.archive = archive
        self._log = get_logger("edx.stages.writer.replicator")

    def run(
        self,
        local_excel_path: Path,
        *,
        run_id: int | None = None,
    ) -> ReplicateOutcome:
        if not self.enabled or self.provider is None:
            self._log.warning("replication_disabled")
            return ReplicateOutcome(
                skipped=True, info=None, reason="disabled or no provider"
            )
        if not self.folder_id:
            self._log.error("replication_no_folder_id")
            return ReplicateOutcome(
                skipped=True, info=None, reason="folder_id is empty"
            )
        if not local_excel_path.is_file():
            self._log.error(
                "replication_local_file_missing", path=str(local_excel_path)
            )
            return ReplicateOutcome(
                skipped=True, info=None, reason="local Excel file missing"
            )

        info = self.provider.upsert_file(
            local_path=local_excel_path,
            remote_folder_id=self.folder_id,
            remote_name=self.file_name,
            archive=self.archive,
        )
        if run_id is not None:
            self.runs_repo.set_drive_link(
                run_id, info.file_id, info.web_view_link
            )
        self._log.info(
            "replication_completed",
            file_id=info.file_id,
            link=info.web_view_link,
            run_id=run_id,
        )
        return ReplicateOutcome(skipped=False, info=info)


def build_replicator_service(
    settings: AppSettings,
    runs_repo: RunsRepo,
) -> ReplicatorService:
    cfg = settings.app.google_drive
    secrets = settings.secrets
    log = get_logger("edx.stages.writer.replicator")

    enabled = cfg.enabled
    provider: CloudStorageProvider | None = None
    if enabled:
        try:
            provider = GoogleDriveProvider.create(
                client_id=(
                    secrets.google_oauth_client_id.get_secret_value()
                    if secrets.google_oauth_client_id is not None
                    else None
                ),
                client_secret=(
                    secrets.google_oauth_client_secret.get_secret_value()
                    if secrets.google_oauth_client_secret is not None
                    else None
                ),
                refresh_token=(
                    secrets.google_oauth_refresh_token.get_secret_value()
                    if secrets.google_oauth_refresh_token is not None
                    else None
                ),
            )
        except GoogleDriveCredentialsMissingError as exc:
            log.warning(
                "google_drive_credentials_missing", error=str(exc)
            )
            enabled = False

    return ReplicatorService(
        provider=provider,
        runs_repo=runs_repo,
        enabled=enabled and provider is not None,
        folder_id=cfg.folder_id,
        file_name=cfg.file_name,
        archive=cfg.archive,
    )
