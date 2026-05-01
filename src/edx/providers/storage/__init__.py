"""Cloud storage providers for the Excel mart (ТЗ §10.4)."""

from edx.providers.storage.base import (
    CloudStorageProvider,
    GoogleDriveCredentialsMissingError,
    RemoteFileInfo,
)
from edx.providers.storage.google_drive import GoogleDriveProvider

__all__ = [
    "CloudStorageProvider",
    "GoogleDriveCredentialsMissingError",
    "GoogleDriveProvider",
    "RemoteFileInfo",
]
