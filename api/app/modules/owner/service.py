"""Owner / document-header service (W3.6)."""
import hashlib
import logging
import secrets
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
            self._db.commit()
        except IntegrityError:
            # Concurrent first-create; fall back to the row that won.
            self._db.rollback()
            profile = self._db.get(OwnerProfile, SINGLETON_PROFILE_ID)
        else:
            self._db.refresh(profile)
        return profile

    def update(self, data: OwnerProfileUpdate) -> OwnerProfile:
        """Apply validated profile fields to the live singleton."""
        profile = self.get_or_create()
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(profile, field, value)
        self._db.commit()
        self._db.refresh(profile)
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
            self._db.flush()
        except IntegrityError:
            # Lost the race to an identical snapshot; reuse the winner.
            self._db.rollback()
            return (
                self._db.query(OwnerProfileSnapshot)
                .filter(OwnerProfileSnapshot.content_hash == content_hash)
                .first()
            )
        return snapshot

    # Render DTO

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

    def upload_logo(self, file_obj: BinaryIO, filename: str, content_type: str) -> OwnerProfile:
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
        self._db.commit()
        self._db.refresh(profile)

        # Best-effort cleanup of the superseded object (after the DB commit so
        # we never orphan the new key).
        if old_key and old_key != new_key:
            self._storage.delete_file(old_key)
        logger.info("Owner logo uploaded")
        return profile

    def remove_logo(self) -> OwnerProfile:
        """Remove the current logo (clears the key and deletes the object)."""
        profile = self.get_or_create()
        old_key = profile.logo_storage_key
        profile.logo_storage_key = None
        self._db.commit()
        self._db.refresh(profile)
        if old_key:
            self._storage.delete_file(old_key)
        logger.info("Owner logo removed")
        return profile
