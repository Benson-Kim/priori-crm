"""Owner / document-header service"""

import hashlib
import logging
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.uploads import validate_upload
from app.constants.enums import DealStage, ModuleKey
from app.constants.settings_defaults import (
    DEFAULT_DEAL_STAGE_PROBABILITIES,
    DEFAULT_ONBOARDING_TASKS,
    DEFAULT_ORG_JURISDICTION,
    DEFAULT_PURCHASE_ORDER_TERMS,
    DEFAULT_REP_QUARTERLY_TARGET_USD,
)
from app.lib.config import settings
from app.lib.storage import StorageService, storage_service
from app.modules.owner.models import (
    SINGLETON_PROFILE_ID,
    OwnerModuleSetting,
    OwnerProfile,
    OwnerProfileSnapshot,
)
from app.modules.owner.schemas import (
    ModuleSettingState,
    OnboardingTemplate,
    OwnerInfo,
    OwnerProfileUpdate,
    PurchaseOrderSettingsDefaults,
    SalesPriceListEntry,
    SalesPricingSettings,
)

logger = logging.getLogger(__name__)

# Fields that constitute the rendered header; the snapshot hash is computed
# over exactly these so two profiles render-identically iff they hash-equal.
# jurisdiction is included because it drives the jurisdiction-aware compliance
# label printed on the document; the two PO-create defaults are NOT
# part of the rendered header and so are deliberately absent here.
_SNAPSHOT_FIELDS = (
    "full_name",
    "location_watermark",
    "address",
    "email",
    "phone",
    "vat_enabled",
    "vat_rate",
    "tax_pin",
    "website",
    "logo_storage_key",
    "jurisdiction",
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

        Thin wrapper over the shared ``storage_tx`` helper: the
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
        updates = data.model_dump(exclude_unset=True)
        template_sent = "onboarding_task_template" in updates
        template = updates.pop("onboarding_task_template", None)
        for field, value in updates.items():
            setattr(profile, field, value)
        if template_sent:
            self._apply_onboarding_template(profile, template)
        self._db.flush()
        logger.info("Owner profile updated")
        return profile

    def _apply_onboarding_template(
        self, profile: OwnerProfile, template: list[str] | None
    ) -> None:
        """Persist a new onboarding task template, bumping its version.

        The version only advances when the RESOLVED template actually
        changes (persisted value, or the built-in default when NULL), so
        re-saving the settings screen unchanged never churns versions.
        Existing checklists are never touched — they copied their tasks at
        create time (template versioning, issue #41).
        """
        current = profile.onboarding_task_template or list(DEFAULT_ONBOARDING_TASKS)
        new = template or list(DEFAULT_ONBOARDING_TASKS)
        if new == current:
            return
        profile.onboarding_task_template = template
        profile.onboarding_template_version += 1
        logger.info(
            "Onboarding task template updated",
            extra={"template_version": profile.onboarding_template_version},
        )

    def onboarding_template(self) -> OnboardingTemplate:
        """Resolve the org onboarding task template + version (issue #41).

        Falls back to the built-in ``DEFAULT_ONBOARDING_TASKS`` (the
        design's 7 seeded tasks, verbatim and in order) when the org never
        configured its own. The onboarding service copies these labels at
        create time only.
        """
        profile = self.get_or_create()
        return OnboardingTemplate(
            tasks=list(profile.onboarding_task_template or DEFAULT_ONBOARDING_TASKS),
            version=profile.onboarding_template_version,
        )

    # Per-owner module entitlements (feature toggles)

    def _module_overrides(self, profile: OwnerProfile) -> dict[str, bool]:
        """Load the explicit per-module overrides for a profile."""
        rows = (
            self._db.query(OwnerModuleSetting)
            .filter(OwnerModuleSetting.owner_profile_id == profile.id)
            .all()
        )
        return {row.module_key: row.enabled for row in rows}

    def module_settings(self) -> list[ModuleSettingState]:
        """Effective state of EVERY module key (admin settings screen).

        Missing override = enabled (default-on). Essential modules are
        always enabled regardless of any (never-created) override row.
        """
        profile = self.get_or_create()
        overrides = self._module_overrides(profile)
        return [
            ModuleSettingState(
                module_key=key.value,
                enabled=True if key.is_essential else overrides.get(key.value, True),
                essential=key.is_essential,
            )
            for key in ModuleKey
        ]

    def enabled_modules_map(self) -> dict[str, bool]:
        """module_key -> effective enabled state, for the bootstrap response."""
        return {s.module_key: s.enabled for s in self.module_settings()}

    def set_module_enabled(
        self, module_key: ModuleKey, enabled: bool
    ) -> ModuleSettingState:
        """Upsert one module override (admin only; audited).

        Disabling an essential module is a 422 — those are core
        infrastructure (auth, owner, health, dashboard) and the router
        gates deliberately do not exist for them.
        """
        from app.common.audit import record_audit_event
        from app.common.exceptions import ValidationException

        if module_key.is_essential and not enabled:
            raise ValidationException(
                detail=(
                    f"The '{module_key.value}' module is essential and "
                    "cannot be disabled."
                ),
                errors=[{"field": "module_key", "reason": "essential_module"}],
            )

        profile = self.get_or_create()
        row = (
            self._db.query(OwnerModuleSetting)
            .filter(
                OwnerModuleSetting.owner_profile_id == profile.id,
                OwnerModuleSetting.module_key == module_key.value,
            )
            .first()
        )
        before_enabled = row.enabled if row is not None else True
        actor_id = getattr(self._current_user, "id", None)

        if row is None:
            row = OwnerModuleSetting(
                owner_profile_id=profile.id,
                module_key=module_key.value,
                enabled=enabled,
                updated_by=actor_id,
            )
            self._db.add(row)
        else:
            row.enabled = enabled
            row.updated_by = actor_id
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=actor_id,
            entity_type="owner_module_setting",
            entity_id=row.id,
            action="module_enabled" if enabled else "module_disabled",
            before={"module_key": module_key.value, "enabled": before_enabled},
            after={"module_key": module_key.value, "enabled": enabled},
        )
        logger.info(
            "Module entitlement changed",
            extra={"module_key": module_key.value, "enabled": enabled},
        )
        return ModuleSettingState(
            module_key=module_key.value,
            enabled=enabled,
            essential=module_key.is_essential,
        )

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

    # Org-scoped Purchase Order settings defaults (PO-11)

    def purchase_order_defaults(self) -> PurchaseOrderSettingsDefaults:
        """Resolve the org-scoped defaults applied when creating a PO.

        Each field falls back to its built-in constant when the org has never
        set it (the column is NULL), so a never-configured organisation still
        gets the documented out-of-the-box values without a backfill. The PO
        service applies these at create time only.
        """
        profile = self.get_or_create()
        return PurchaseOrderSettingsDefaults(
            terms_and_conditions=(
                profile.default_terms_and_conditions or DEFAULT_PURCHASE_ORDER_TERMS
            ),
            send_message=profile.default_send_message,
            jurisdiction=profile.jurisdiction or DEFAULT_ORG_JURISDICTION,
        )

    # Sales Desk pricing settings (issue #44)

    @staticmethod
    def sales_pricing_settings() -> SalesPricingSettings:
        """Resolve the org sales pricing settings + derived price list.

        The documented price-list source for the Quotes & pricing UI
        (#49). Built from the seeded constants
        (``app.constants.settings_defaults``); the derived Annual/seat
        (monthly x 12 at list) and 10-seat ARR (monthly x 12 x 10) columns
        match ``Quotes___Pricing.svg`` verbatim. Values are decimal
        strings — no floats anywhere.
        """
        from decimal import Decimal

        from app.constants.settings_defaults import (
            DEFAULT_ANNUAL_BILLING_DISCOUNT_PCT,
            DEFAULT_FX_UNITS_PER_USD,
            DEFAULT_PRODUCT_CATALOG,
        )

        entries = []
        for item in DEFAULT_PRODUCT_CATALOG:
            monthly = Decimal(item["usd_per_seat_month"])
            entries.append(
                SalesPriceListEntry(
                    name=item["name"],
                    usd_per_seat_month=str(monthly),
                    usd_per_seat_year=str(monthly * 12),
                    usd_ten_seat_arr=str(monthly * 12 * 10),
                )
            )
        return SalesPricingSettings(
            catalog=entries,
            annual_billing_discount_pct=DEFAULT_ANNUAL_BILLING_DISCOUNT_PCT,
            fx_units_per_usd=dict(DEFAULT_FX_UNITS_PER_USD),
        )

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
            vat_enabled=source.vat_enabled,
            vat_rate=source.vat_rate,
            tax_pin=source.tax_pin,
            website=source.website,
            logo_storage_key=source.logo_storage_key,
            jurisdiction=getattr(source, "jurisdiction", None),
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


# Sales Desk analytics settings resolution (issue #45)
#
# Module-level, read-only resolvers so read-side consumers (deal responses,
# the Sales Desk dashboard under its READ ONLY snapshot transaction) can
# resolve the org settings WITHOUT ever creating the singleton row.


def resolve_deal_stage_probabilities(db: Session) -> dict[DealStage, Decimal]:
    """Per-stage win probabilities: persisted owner setting or the defaults.

    Values are validated on write (OwnerProfileUpdate); any missing or
    malformed persisted entry falls back to the built-in default for that
    stage so a partial/legacy value can never break the read side.
    """
    profile = db.get(OwnerProfile, SINGLETON_PROFILE_ID)
    raw = (profile.deal_stage_probabilities if profile else None) or {}
    resolved: dict[DealStage, Decimal] = {}
    for stage in DealStage:
        value = raw.get(stage.value, DEFAULT_DEAL_STAGE_PROBABILITIES[stage.value])
        try:
            probability = Decimal(str(value))
        except ArithmeticError:
            probability = Decimal(DEFAULT_DEAL_STAGE_PROBABILITIES[stage.value])
        resolved[stage] = probability
    return resolved


def resolve_rep_quarterly_targets(
    db: Session,
) -> tuple[dict[uuid.UUID, Decimal], Decimal]:
    """Per-rep quarterly won-value targets (USD) + the default for the rest.

    Returns ``(targets, default)``: reps without an entry use ``default``
    (DEFAULT_REP_QUARTERLY_TARGET_USD). Malformed persisted entries are
    skipped (write-side validation makes them unreachable in practice).
    """
    profile = db.get(OwnerProfile, SINGLETON_PROFILE_ID)
    raw = (profile.rep_quarterly_targets if profile else None) or {}
    targets: dict[uuid.UUID, Decimal] = {}
    for key, value in raw.items():
        try:
            targets[uuid.UUID(str(key))] = Decimal(str(value))
        except (ValueError, ArithmeticError):
            logger.warning("Skipping malformed rep quarterly target entry")
            continue
    return targets, Decimal(DEFAULT_REP_QUARTERLY_TARGET_USD)
