"""Owner / document-header service"""

import hashlib
import logging
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.uploads import validate_upload
from app.lib.config import settings
from app.lib.storage import StorageService, storage_service
from app.modules.owner.models import (
    SINGLETON_PROFILE_ID,
    OwnerProfile,
    OwnerProfileSnapshot,
)
from app.modules.owner.schemas import OwnerInfo, OwnerProfileUpdate

logger = logging.getLogger(__name__)

# Fields that constitute the rendered header; the snapshot hash is computed
# over exactly these so two profiles render-identically iff they hash-equal.
_SNAPSHOT_FIELDS = (
    "full_name",
    "location_watermark",
    "address",
    "email",
    "phone",
    "tax_pin",
    "website",
    "logo_storage_key",
)

_LOGO_DIRECTORY = "owner/logo"


@dataclass(frozen=True)
class LogoDownload:
    """Result of serving the owner logo.

    Exactly one of ``presigned_url`` / ``stream`` is set: a backend that can
    mint a presigned URL (S3) returns the URL so the download bypasses the
    app worker; otherwise the bytes are streamed through the app (local).
    """

    presigned_url: str | None = None
    stream: Iterator[bytes] | None = None
    media_type: str = "application/octet-stream"


class OwnerService:
    """Manage the single live owner profile and its immutable snapshots."""

    def __init__(
        self,
        db: Session,
        current_user=None,
        storage: StorageService | None = None,
    ) -> None:
        self._db = db
        self._current_user = current_user
        self._storage = storage or storage_service

    # Transaction-safe storage cleanup

    def _schedule_object_cleanup(self, *keys: str | None) -> None:
        """Delete superseded storage objects only after the tx commits.

        Thin wrapper over the shared ``storage_tx`` helper (ISSUE-006): the
        service flushes but never commits (the request-scoped ``get_db`` owns
        the commit), so the old object is only deleted once the outer
        transaction actually commits — a later rollback never orphans the new
        key. Best-effort: a failed delete is logged, never raised.
        """
        from app.common.storage_tx import schedule_delete_on_commit

        schedule_delete_on_commit(self._db, self._storage, *keys)

    # Live profile

    def get_or_create(self) -> OwnerProfile:
        """Return the live singleton, seeding it from settings on first use."""
        profile = self._db.get(OwnerProfile, SINGLETON_PROFILE_ID)
        if profile is not None:
            return profile

        profile = OwnerProfile(
            id=SINGLETON_PROFILE_ID,
            full_name=settings.APP_NAME,
        )
        self._db.add(profile)
        try:
            # SAVEPOINT: a lost first-create race rolls back only this insert,
            # leaving the surrounding transaction usable.
            with self._db.begin_nested():
                self._db.flush()
        except IntegrityError:
            # Concurrent first-create; fall back to the row that won.
            profile = self._db.get(OwnerProfile, SINGLETON_PROFILE_ID)
        return profile

    def update(self, data: OwnerProfileUpdate) -> OwnerProfile:
        """Apply validated profile fields to the live singleton."""
        profile = self.get_or_create()
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(profile, field, value)
        self._db.flush()
        logger.info("Owner profile updated")
        return profile

    # Snapshots (immutable)

    @staticmethod
    def _content_hash(profile: OwnerProfile) -> str:
        """Stable digest over the rendered fields of a profile."""
        parts = [str(getattr(profile, f) or "") for f in _SNAPSHOT_FIELDS]
        joined = "\x1f".join(parts)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def snapshot_current(self) -> OwnerProfileSnapshot:
        """Return an immutable snapshot of the current live profile.

        Content-addressed: an unchanged profile reuses its existing snapshot
        row instead of creating a duplicate, so repeatedly issuing documents
        does not grow the table.
        """
        profile = self.get_or_create()
        content_hash = self._content_hash(profile)

        existing = (
            self._db.query(OwnerProfileSnapshot)
            .filter(OwnerProfileSnapshot.content_hash == content_hash)
            .first()
        )
        if existing is not None:
            return existing

        snapshot = OwnerProfileSnapshot(
            content_hash=content_hash,
            **{f: getattr(profile, f) for f in _SNAPSHOT_FIELDS},
        )
        self._db.add(snapshot)
        try:
            # SAVEPOINT so a lost race does not poison the outer transaction.
            with self._db.begin_nested():
                self._db.flush()
        except IntegrityError:
            # Lost the race to an identical snapshot; reuse the winner.
            return (
                self._db.query(OwnerProfileSnapshot)
                .filter(OwnerProfileSnapshot.content_hash == content_hash)
                .first()
            )
        return snapshot

    # Render DTO

    def load_logo_bytes(
        self, source: OwnerProfile | OwnerProfileSnapshot | None
    ) -> bytes | None:
        """Read the raw logo image bytes for a profile/snapshot.

        Used by PDF rendering to embed the logo. Reads through the storage
        facade so it works for both the local and S3 backends. Fails soft:
        any missing key or storage error returns ``None`` so document
        generation degrades to a text-only header rather than erroring.
        """
        key = getattr(source, "logo_storage_key", None)
        if not key:
            return None
        try:
            with self._storage.open_file(key) as handle:
                return handle.read()
        except Exception:
            logger.warning("Owner logo could not be read for PDF embed", exc_info=True)
            return None

    @staticmethod
    def to_owner_info(source: OwnerProfile | OwnerProfileSnapshot | None) -> OwnerInfo:
        """Build the render DTO from a profile/snapshot, or a settings fallback."""
        if source is None:
            return OwnerInfo(full_name=settings.APP_NAME)
        return OwnerInfo(
            full_name=source.full_name,
            location_watermark=source.location_watermark,
            address=source.address,
            email=source.email,
            phone=source.phone,
            tax_pin=source.tax_pin,
            website=source.website,
            logo_storage_key=source.logo_storage_key,
        )

    # Logo

    def upload_logo(
        self, file_obj: BinaryIO, filename: str, content_type: str
    ) -> OwnerProfile:
        """Validate and store a new logo, replacing any previous one."""
        safe_basename, _ext, _size = validate_upload(
            file_obj, filename or "logo", content_type or "application/octet-stream"
        )
        unique = secrets.token_hex(8)
        stored_name = f"{unique}_{safe_basename}"
        new_key = self._storage.upload_file(file_obj, _LOGO_DIRECTORY, stored_name)

        profile = self.get_or_create()
        old_key = profile.logo_storage_key
        profile.logo_storage_key = new_key
        self._db.flush()

        # Delete the superseded object only once the outer transaction has
        # committed, so a later rollback never orphans the new key.
        if old_key and old_key != new_key:
            self._schedule_object_cleanup(old_key)
        logger.info("Owner logo uploaded")
        return profile

    def remove_logo(self) -> OwnerProfile:
        """Remove the current logo (clears the key and deletes the object)."""
        profile = self.get_or_create()
        old_key = profile.logo_storage_key
        profile.logo_storage_key = None
        self._db.flush()
        if old_key:
            self._schedule_object_cleanup(old_key)
        logger.info("Owner logo removed")
        return profile

    def serve_logo(self) -> LogoDownload:
        """Serve the current owner logo.

        Returns a presigned URL when the backend supports it (S3), otherwise a
        byte stream the router can return directly. Keeps all storage access
        inside the service so callers never touch the storage backend.

        Raises NotFoundException when no logo is set.
        """
        from app.common.exceptions import NotFoundException

        profile = self.get_or_create()
        key = profile.logo_storage_key
        if not key:
            raise NotFoundException(detail="No logo set", resource="logo")

        presigned = self._storage.presigned_url(key)
        if presigned:
            return LogoDownload(presigned_url=presigned)
        return LogoDownload(stream=self._storage.download_stream(key))
