"""
Object storage service abstraction (W3.3 / P-13 / V-SCALE-2).

A single public ``StorageService`` facade delegates to a pluggable backend
selected by ``settings.STORAGE_BACKEND``:

- ``LocalStorageBackend`` — single-node filesystem. All path construction is
  funnelled through ``_safe_path`` / ``resolve_safe_path`` so a maliciously
  crafted directory or filename (``../``, absolute paths, symlinks) can never
  escape ``base_dir`` on upload, serve, or delete.
- ``S3StorageBackend`` — S3 / S3-compatible object store, required for
  horizontal scaling (uploads written on one instance are visible to all).

In both backends ``storage_key`` is an opaque, sanitized *relative* key
(e.g. ``expenses/<id>/<hex>_name.pdf``) — never a servable absolute local
path (EXP-SEC-1). Every key is reduced to safe per-segment basenames and
checked for containment before any I/O, so the S3 backend inherits the same
guarantees as local.
"""
import shutil
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from app.common.exceptions import BadRequestException, NotFoundException
from app.common.uploads import sanitize_basename
from app.lib.config import settings


def sanitize_storage_key(*parts: str) -> str:
    """Build a safe, contained relative storage key from path segments.

    Each segment is split on ``/`` and reduced to a safe basename, dropping
    empty/``.``/``..`` components. The result never starts with ``/`` and
    cannot traverse upwards, so it is valid both as a path relative to a
    local base dir and as an S3 object key.
    """
    safe_segments: list[str] = []
    for part in parts:
        if not part:
            continue
        for segment in str(part).replace("\\", "/").split("/"):
            if segment in ("", ".", ".."):
                continue
            safe_segments.append(sanitize_basename(segment))
    if not safe_segments:
        raise BadRequestException(detail="Invalid storage path.", field="storage_key")
    return str(PurePosixPath(*safe_segments))


class StorageBackend(Protocol):
    """Common interface implemented by every storage backend."""

    def upload_file(self, file_obj: BinaryIO, directory: str, filename: str) -> str: ...

    def open_file(self, storage_key: str) -> BinaryIO: ...

    def download_stream(self, storage_key: str) -> Iterator[bytes]: ...

    def delete_file(self, storage_key: str) -> bool: ...

    def file_exists(self, storage_key: str) -> bool: ...

    def presigned_url(self, storage_key: str) -> str | None: ...


class LocalStorageBackend:
    """Filesystem backend confined to ``base_dir`` (single-node)."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.UPLOAD_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _is_contained(self, candidate: Path) -> bool:
        """True if ``candidate`` is ``base_dir`` itself or strictly inside it."""
        return candidate == self.base_dir or self.base_dir in candidate.parents

    def _safe_path(self, *parts: str) -> Path:
        """Resolve ``parts`` under ``base_dir`` and assert containment."""
        key = sanitize_storage_key(*parts)
        candidate = (self.base_dir / key).resolve()
        if not self._is_contained(candidate):
            raise BadRequestException(detail="Invalid storage path.", field="storage_key")
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
            raise BadRequestException(detail="Invalid storage path.", field="storage_key")
        return candidate

    def upload_file(self, file_obj: BinaryIO, directory: str, filename: str) -> str:
        """Write a file and return its storage key (relative to ``base_dir``)."""
        target = self._safe_path(directory, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as buffer:
            shutil.copyfileobj(file_obj, buffer)
        return str(target.relative_to(self.base_dir).as_posix())

    def open_file(self, storage_key: str) -> BinaryIO:
        """Open a stored object for reading via a validated, contained path."""
        path = self.resolve_safe_path(storage_key)
        if not path.is_file():
            raise NotFoundException(detail="File not found on server", resource="file")
        return open(path, "rb")

    def download_stream(self, storage_key: str) -> Iterator[bytes]:
        """Yield the object's bytes in chunks from the contained path."""
        handle = self.open_file(storage_key)
        try:
            while chunk := handle.read(64 * 1024):
                yield chunk
        finally:
            handle.close()

    def delete_file(self, storage_key: str) -> bool:
        """Delete a file. True if deleted or already absent; False on bad key."""
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
        """Check if file exists (within the contained base dir)."""
        try:
            return self.resolve_safe_path(storage_key).is_file()
        except BadRequestException:
            return False

    def presigned_url(self, storage_key: str) -> str | None:
        """Local files are served through the app, not a presigned URL."""
        return None


class S3StorageBackend:
    """S3 / S3-compatible object-store backend (multi-instance safe).

    Objects are stored under sanitized, contained keys. Downloads stream
    straight from ``get_object`` (no local disk), and a presigned GET URL
    can be minted so large downloads bypass the app worker entirely.
    """

    def __init__(self, bucket: str | None = None) -> None:
        import boto3

        self.bucket = bucket or settings.S3_BUCKET
        if not self.bucket:
            raise BadRequestException(
                detail="S3_BUCKET must be configured for the S3 storage backend.",
                field="S3_BUCKET",
            )
        self._client = boto3.client(
            "s3",
            region_name=settings.S3_REGION or settings.AWS_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        )

    def upload_file(self, file_obj: BinaryIO, directory: str, filename: str) -> str:
        """Upload an object and return its key."""
        key = sanitize_storage_key(directory, filename)
        file_obj.seek(0)
        self._client.upload_fileobj(file_obj, self.bucket, key)
        return key

    def _get_object(self, storage_key: str):  # noqa: ANN202 - boto3 response dict
        from botocore.exceptions import ClientError

        key = sanitize_storage_key(storage_key)
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise NotFoundException(
                    detail="File not found in storage", resource="file"
                ) from exc
            raise

    def open_file(self, storage_key: str) -> BinaryIO:
        """Return a readable binary stream for the object body."""
        return self._get_object(storage_key)["Body"]

    def download_stream(self, storage_key: str) -> Iterator[bytes]:
        """Yield the object's bytes in chunks straight from S3."""
        body = self._get_object(storage_key)["Body"]
        try:
            yield from body.iter_chunks(chunk_size=64 * 1024)
        finally:
            body.close()

    def delete_file(self, storage_key: str) -> bool:
        """Delete an object. True on success (idempotent); False on bad key."""
        try:
            key = sanitize_storage_key(storage_key)
        except BadRequestException:
            return False
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 - storage cleanup is best-effort
            return False

    def file_exists(self, storage_key: str) -> bool:
        """Check whether the object exists via head_object."""
        from botocore.exceptions import ClientError

        try:
            key = sanitize_storage_key(storage_key)
        except BadRequestException:
            return False
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def presigned_url(self, storage_key: str) -> str | None:
        """Mint a time-limited presigned GET URL for the object."""
        key = sanitize_storage_key(storage_key)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=settings.S3_PRESIGN_EXPIRY,
        )


def _build_backend() -> StorageBackend:
    """Construct the configured storage backend."""
    if settings.STORAGE_BACKEND == "s3":
        return S3StorageBackend()
    return LocalStorageBackend()


class StorageService:
    """Public storage facade delegating to the configured backend.

    The method surface (upload_file / open_file / download_stream /
    delete_file / file_exists / presigned_url) is identical across backends
    so callers (routers, the forthcoming owner-logo uploader) never depend
    on whether storage is local or S3.
    """

    def __init__(
        self,
        backend: StorageBackend | None = None,
        base_dir: str | None = None,
    ) -> None:
        if backend is not None:
            self._backend = backend
        elif base_dir is not None:
            # Backward-compatible explicit local backend.
            self._backend = LocalStorageBackend(base_dir=base_dir)
        else:
            self._backend = _build_backend()

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    def upload_file(self, file_obj: BinaryIO, directory: str, filename: str) -> str:
        return self._backend.upload_file(file_obj, directory, filename)

    def open_file(self, storage_key: str) -> BinaryIO:
        return self._backend.open_file(storage_key)

    def download_stream(self, storage_key: str) -> Iterator[bytes]:
        return self._backend.download_stream(storage_key)

    def delete_file(self, storage_key: str) -> bool:
        return self._backend.delete_file(storage_key)

    def file_exists(self, storage_key: str) -> bool:
        return self._backend.file_exists(storage_key)

    def presigned_url(self, storage_key: str) -> str | None:
        """Return a presigned download URL when the backend supports it."""
        return self._backend.presigned_url(storage_key)


storage_service = StorageService()
