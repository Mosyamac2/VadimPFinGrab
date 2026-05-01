"""Factory for the Unpacker stage."""

from __future__ import annotations

from edx.config import AppSettings
from edx.stages.unpacker.service import UnpackerService
from edx.storage import Database, DocumentsRepo, PublicationsRepo


def build_unpacker_service(
    settings: AppSettings,
    db: Database,
    publications_repo: PublicationsRepo,
    documents_repo: DocumentsRepo,
) -> UnpackerService:
    return UnpackerService(
        db=db,
        publications_repo=publications_repo,
        documents_repo=documents_repo,
        raw_dir=settings.app.paths.raw_dir,
        max_unpacked_mb=settings.app.unpacker.max_unpacked_mb,
    )
