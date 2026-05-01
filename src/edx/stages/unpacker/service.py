"""Unpacker stage: extract archives and inventory the publication directory.

For each publication:
1. Find ``.zip`` / ``.rar`` archives directly inside the publication folder.
2. Extract them into ``_unpacked/`` after validating each member's path
   (no ``..`` traversal, no absolute paths) and the cumulative uncompressed
   size against ``unpacker.max_unpacked_mb``.
3. Inventory **every** non-archive file in the publication directory (whether
   it came from an archive or was downloaded directly) into the ``documents``
   table.
4. Mark the publication as ``unpacked``.

If extraction trips a safety check, the publication is marked ``failed`` and
no documents are written for it.
"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from edx.logging_setup import get_logger
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    PublicationRow,
    PublicationsRepo,
)

UNPACKED_SUBDIR: Final[str] = "_unpacked"
SUPPORTED_ARCHIVE_SUFFIXES: Final[frozenset[str]] = frozenset({".zip", ".rar"})


class UnpackerError(RuntimeError):
    """Raised when extraction trips a safety check (traversal, size limit)."""


@dataclass(frozen=True)
class UnpackOutcome:
    publication_id: str
    archives_extracted: int
    documents_added: int
    skipped: bool


class UnpackerService:
    def __init__(
        self,
        db: Database,
        publications_repo: PublicationsRepo,
        documents_repo: DocumentsRepo,
        *,
        raw_dir: Path,
        max_unpacked_mb: int = 500,
    ) -> None:
        if max_unpacked_mb < 1:
            raise ValueError("max_unpacked_mb must be >= 1")
        self.db = db
        self.publications_repo = publications_repo
        self.documents_repo = documents_repo
        self.raw_dir = Path(raw_dir)
        self.max_unpacked_bytes = max_unpacked_mb * 1024 * 1024
        self._log = get_logger("edx.stages.unpacker")

    def run(
        self, publications: Iterable[PublicationRow]
    ) -> list[UnpackOutcome]:
        outcomes: list[UnpackOutcome] = []
        for pub in publications:
            outcome = self._unpack_one(pub)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _unpack_one(self, pub: PublicationRow) -> UnpackOutcome | None:
        pub_dir = self.raw_dir / pub.ticker / pub.publication_id
        if not pub_dir.exists():
            self._log.warning(
                "unpack_pub_dir_missing",
                publication_id=pub.publication_id,
                path=str(pub_dir),
            )
            self.publications_repo.mark_status(
                pub.publication_id, "failed", error="raw directory missing"
            )
            return None

        unpacked_dir = pub_dir / UNPACKED_SUBDIR
        archives = sorted(
            p
            for p in pub_dir.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_ARCHIVE_SUFFIXES
        )

        archives_extracted = 0
        if archives:
            unpacked_dir.mkdir(parents=True, exist_ok=True)
            try:
                for archive in archives:
                    self._extract(archive, unpacked_dir)
                    archives_extracted += 1
            except UnpackerError as exc:
                self._log.error(
                    "unpack_failed",
                    publication_id=pub.publication_id,
                    archive=str(exc),
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                # Best-effort cleanup of partially-extracted contents.
                shutil.rmtree(unpacked_dir, ignore_errors=True)
                return UnpackOutcome(
                    publication_id=pub.publication_id,
                    archives_extracted=archives_extracted,
                    documents_added=0,
                    skipped=False,
                )

        documents = self._inventory(pub_dir, archives_set=set(archives))
        if documents:
            self.documents_repo.add_documents(pub.publication_id, documents)

        self.publications_repo.mark_status(pub.publication_id, "unpacked")
        self._log.info(
            "publication_unpacked",
            publication_id=pub.publication_id,
            archives_extracted=archives_extracted,
            documents_added=len(documents),
        )
        return UnpackOutcome(
            publication_id=pub.publication_id,
            archives_extracted=archives_extracted,
            documents_added=len(documents),
            skipped=False,
        )

    def _extract(self, archive: Path, target_dir: Path) -> None:
        suffix = archive.suffix.lower()
        if suffix == ".zip":
            self._extract_zip(archive, target_dir)
        elif suffix == ".rar":
            self._extract_rar(archive, target_dir)
        else:
            self._log.warning(
                "unsupported_archive_suffix", archive=str(archive)
            )

    def _extract_zip(self, archive: Path, target_dir: Path) -> None:
        target_resolved = target_dir.resolve()
        total_bytes = 0
        with zipfile.ZipFile(archive, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                self._validate_member_path(member.filename, archive)
                total_bytes += member.file_size
                if total_bytes > self.max_unpacked_bytes:
                    raise UnpackerError(
                        f"{archive.name}: uncompressed size exceeds "
                        f"{self.max_unpacked_bytes // (1024 * 1024)} MB cap"
                    )
                dest = (target_dir / member.filename).resolve()
                if not _is_inside(dest, target_resolved):
                    raise UnpackerError(
                        f"{archive.name}: zip member would escape target dir: "
                        f"{member.filename!r}"
                    )
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _extract_rar(self, archive: Path, target_dir: Path) -> None:
        try:
            import rarfile  # imported lazily so missing unrar binary is soft-fail
        except ImportError as exc:  # pragma: no cover - install-time concern
            raise UnpackerError(f"rarfile not installed: {exc}") from exc

        if shutil.which("unrar") is None and shutil.which("unar") is None:
            self._log.warning(
                "rar_unrar_binary_missing",
                archive=str(archive),
            )
            raise UnpackerError(
                "RAR archive present but neither 'unrar' nor 'unar' is on PATH"
            )

        target_resolved = target_dir.resolve()
        total_bytes = 0
        try:
            with rarfile.RarFile(archive) as rf:
                for member in rf.infolist():
                    if member.is_dir():
                        continue
                    self._validate_member_path(member.filename, archive)
                    total_bytes += member.file_size
                    if total_bytes > self.max_unpacked_bytes:
                        raise UnpackerError(
                            f"{archive.name}: uncompressed size exceeds "
                            f"{self.max_unpacked_bytes // (1024 * 1024)} MB cap"
                        )
                    dest = (target_dir / member.filename).resolve()
                    if not _is_inside(dest, target_resolved):
                        raise UnpackerError(
                            f"{archive.name}: rar member would escape target "
                            f"dir: {member.filename!r}"
                        )
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with rf.open(member) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        except rarfile.Error as exc:
            raise UnpackerError(f"{archive.name}: rar extraction failed: {exc}") from exc

    def _validate_member_path(self, member_name: str, archive: Path) -> None:
        if not member_name:
            raise UnpackerError(
                f"{archive.name}: archive contains an empty member name"
            )
        normalized = member_name.replace("\\", "/")
        if normalized.startswith("/"):
            raise UnpackerError(
                f"{archive.name}: archive member uses an absolute path: "
                f"{member_name!r}"
            )
        parts = [part for part in normalized.split("/") if part]
        if any(part == ".." for part in parts):
            raise UnpackerError(
                f"{archive.name}: archive member path contains '..' "
                f"traversal: {member_name!r}"
            )

    def _inventory(
        self, pub_dir: Path, *, archives_set: set[Path]
    ) -> list[DocumentInput]:
        out: list[DocumentInput] = []
        for path in sorted(pub_dir.rglob("*")):
            if not path.is_file():
                continue
            if path in archives_set:
                continue
            if path.name.endswith(".partial"):
                continue
            mime, _ = mimetypes.guess_type(path.name)
            out.append(
                DocumentInput(
                    relative_path=str(path.relative_to(pub_dir)),
                    file_hash=_sha256_of_file(path),
                    mime_type=mime,
                )
            )
        return out


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _sha256_of_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
