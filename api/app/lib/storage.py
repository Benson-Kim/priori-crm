"""
Object storage service abstraction.
Currently backed by local filesystem but designed to support S3-compatible APIs later.

All path construction is funnelled through ``_safe_path`` / ``resolve_safe_path``
so a maliciously crafted directory or filename (``../``, absolute paths,
symlinks) can never escape ``base_dir`` on upload, serve, or delete (P-13).
"""
import shutil
from pathlib import Path
from typing import BinaryIO

from app.common.exceptions import BadRequestException, NotFoundException
from app.common.uploads import sanitize_basename
from app.lib.config import settings


class StorageService:
    """Service to handle file uploads and downloads on a contained base dir."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.UPLOAD_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _is_contained(self, candidate: Path) -> bool:
        """True if ``candidate`` is ``base_dir`` itself or strictly inside it."""
        return candidate == self.base_dir or self.base_dir in candidate.parents

    def _safe_path(self, *parts: str) -> Path:
        """Resolve ``parts`` under ``base_dir`` and assert containment.

        Each component is reduced to a safe basename, then the fully
        resolved path is checked to be inside ``base_dir``. Any attempt to
        traverse out (``..``, absolute path, symlink escape) raises
        BadRequestException rather than touching the filesystem.
        """
        safe_parts = [
            sanitize_basename(part) for part in parts if part not in ("", None)
        ]
        candidate = self.base_dir.joinpath(*safe_parts).resolve()
        if not self._is_contained(candidate):
            raise BadRequestException(
                detail="Invalid storage path.", field="storage_key"
            )
        return candidate

    def resolve_safe_path(self, storage_key: str) -> Path:
        """Resolve a stored key and assert it stays within ``base_dir``.

        ``storage_key`` may be a multi-segment relative path produced by
        ``upload_file`` or, for backward compatibility, an absolute path
        that already points inside ``base_dir``.
        """
        raw = Path(storage_key)
        candidate = raw.resolve() if raw.is_absolute() else (self.base_dir / raw).resolve()
        if not self._is_contained(candidate):
            raise BadRequestException(
                detail="Invalid storage path.", field="storage_key"
            )
        return candidate

    def upload_file(self, file_obj: BinaryIO, directory: str, filename: str) -> str:
        """
        Upload a file to storage and return the storage key (path relative
        to ``base_dir``). Path components are sanitized and contained.
        """
        target = self._safe_path(directory, filename)
        target.parent.mkdir(parents=True, exist_ok=True)

        with open(target, "wb") as buffer:
            shutil.copyfileobj(file_obj, buffer)

        return str(target.relative_to(self.base_dir))

    def open_file(self, storage_key: str) -> BinaryIO:
        """Open a stored object for reading via a validated, contained path."""
        path = self.resolve_safe_path(storage_key)
        if not path.is_file():
            raise NotFoundException(
                detail="File not found on server", resource="file"
            )
        return open(path, "rb")

    def delete_file(self, storage_key: str) -> bool:
        """
        Delete a file from storage. Returns True if successfully deleted or
        already absent. Invalid/traversal keys are refused (return False).
        """
        try:
            path = self.resolve_safe_path(storage_key)
        except BadRequestException:
            return False
        if not path.exists():
            return True
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def file_exists(self, storage_key: str) -> bool:
        """Check if file exists in storage (within the contained base dir)."""
        try:
            return self.resolve_safe_path(storage_key).is_file()
        except BadRequestException:
            return False


storage_service = StorageService()
