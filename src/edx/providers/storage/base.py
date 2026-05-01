"""Cloud storage provider interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RemoteFileInfo:
    file_id: str
    web_view_link: str
    updated_at: str


class GoogleDriveCredentialsMissingError(RuntimeError):
    """Raised when GoogleDriveProvider.create is asked to build with missing keys."""


@runtime_checkable
class CloudStorageProvider(Protocol):
    """Stable surface for swappable cloud-storage backends."""

    name: str

    def upsert_file(
        self,
        local_path: Path,
        remote_folder_id: str,
        remote_name: str,
        *,
        archive: bool,
    ) -> RemoteFileInfo: ...
