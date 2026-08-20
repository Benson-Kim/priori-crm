"""Operator MFA service (ADR-0014, issue #73).

Enrollment, activation, login second-factor verification and step-up
proof verification for platform_operator accounts. Applies only to the
already-seeded operator acting on itself — there is no creation or
promotion path here (QA finding 09).

Transaction discipline: success paths flush and let CommitOnSuccessRoute
own the commit, EXCEPT step-up verification, which commits its own
outcome (replay fence + audit event) so that neither a later rollback of
the destructive action nor the 401 raised on denial can erase the
evidence — the same explicit-commit-then-raise pattern the OTP
attempt counter uses (app/modules/auth/service.py).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.exceptions import (
    BadRequestException,
    RateLimitException,
    UnauthorizedException,
)
from app.common.mfa import (
    build_otpauth_uri,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    looks_like_recovery_code,
    verify_totp,
)
from app.constants.enums import OperatorMfaStatus
from app.lib.config import settings
from app.modules.platform.models import OperatorMfaTotp, OperatorRecoveryCode

logger = logging.getLogger(__name__)

#: Audit entity type for every operator-MFA event; entity_id is the
#: operator's user id. Listed in PLATFORM_AUDIT_ENTITY_TYPES
#: (app/modules/owner/service.py) so GET /platform/audit surfaces it.
MFA_AUDIT_ENTITY_TYPE = "operator_mfa"

#: Clear (non-generic) step-up failure message: step-up runs on an
#: AUTHENTICATED operator session, so — mirroring the suspension-403
#: precedent from !77 — there is no enumeration surface to protect here.
#: The unauthenticated login flow, by contrast, uses the auth service's
#: single generic message for MFA failures.
_STEP_UP_ERROR = (
    "A valid authenticator code or recovery code (X-MFA-Code header) is "
    "required for this action."
)


class OperatorMfaService:
    """TOTP enrollment + verification for the authenticated operator."""

    def __init__(self, db: Session, current_user=None) -> None:
        self._db = db
        self._current_user = current_user

    # Reads

    def enrollment_row(self, user_id: uuid.UUID) -> OperatorMfaTotp | None:
        """The operator's enrollment row, if any (pending or active)."""
        return (
            self._db.query(OperatorMfaTotp)
            .filter(OperatorMfaTotp.user_id == user_id)
            .first()
        )

    def is_enrolled(self, user_id: uuid.UUID) -> bool:
        """Whether the operator has a CONFIRMED (active) enrollment."""
        row = self.enrollment_row(user_id)
        return row is not None and row.status == OperatorMfaStatus.ACTIVE

    def status(self) -> dict:
        """Enrollment status for GET /platform/mfa."""
        row = self.enrollment_row(self._current_user.id)
        return {
            "enrolled": row is not None and row.status == OperatorMfaStatus.ACTIVE,
            "pending": row is not None and row.status == OperatorMfaStatus.PENDING,
            "recovery_codes_remaining": self._unused_recovery_codes(
                self._current_user.id
            ).count(),
        }

    # Enrollment

    def start_enrollment(self, step_up_code: str | None = None) -> tuple[str, str]:
        """Provision (or rotate) a TOTP seed; returns (secret, otpauth URI).

        Acts on the authenticated operator only. While an enrollment is
        merely PENDING it can be freely regenerated (nothing is enforced
        yet). Rotating an ACTIVE enrollment demands a fresh step-up proof
        first — otherwise a hijacked full session could silently swap the
        second factor.

        The returned plaintext secret exists only in this response; only
        the Fernet ciphertext is stored, and nothing is logged.
        """
        user = self._current_user
        row = self.enrollment_row(user.id)
        rotation = row is not None and row.status == OperatorMfaStatus.ACTIVE
        if rotation:
            # Same throttle + audit-on-denial semantics as any step-up.
            self.verify_step_up(step_up_code, action="mfa_rotation")

        secret = generate_totp_secret()
        if row is None:
            row = OperatorMfaTotp(
                user_id=user.id,
                secret_encrypted=encrypt_totp_secret(secret),
            )
            self._db.add(row)
        else:
            row.secret_encrypted = encrypt_totp_secret(secret)
        row.status = OperatorMfaStatus.PENDING.value
        row.last_used_counter = None
        row.confirmed_at = None
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=user.id,
            entity_type=MFA_AUDIT_ENTITY_TYPE,
            entity_id=user.id,
            action="mfa_enrollment_started",
            after={"rotation": rotation},
        )
        logger.info(
            "Operator MFA enrollment started",
            extra={"user_id": str(user.id), "rotation": rotation},
        )
        return secret, build_otpauth_uri(secret, user.email, settings.APP_NAME)

    def activate_enrollment(self, code: str) -> list[str]:
        """Confirm a pending enrollment with a live code; issue recovery codes.

        On success the enrollment becomes ACTIVE (MFA is REQUIRED for this
        operator's token issuance from now on), the matched counter seeds
        the replay fence, any previous recovery codes are invalidated and
        a fresh set is returned — plaintext exists only in this response.

        On a wrong code the failure is audited and committed before the
        401 propagates (deliberate extension of the successful-writes-only
        trail; ADR-0014 justifies it).
        """
        user = self._current_user
        self._enforce_attempt_throttle(user.id)

        row = self.enrollment_row(user.id)
        if row is None or row.status != OperatorMfaStatus.PENDING:
            raise BadRequestException(
                detail=(
                    "No pending MFA enrollment to activate. Start enrollment "
                    "first (POST /platform/mfa/enrollment)."
                )
            )

        secret = decrypt_totp_secret(row.secret_encrypted)
        matched = verify_totp(secret, code) if secret else None
        if matched is None:
            record_audit_event(
                self._db,
                actor_id=user.id,
                entity_type=MFA_AUDIT_ENTITY_TYPE,
                entity_id=user.id,
                action="mfa_activation_failed",
                after=None,
            )
            self._db.commit()
            raise UnauthorizedException(
                "The authenticator code is invalid or has expired."
            )

        row.status = OperatorMfaStatus.ACTIVE.value
        row.confirmed_at = datetime.now(UTC)
        row.last_used_counter = matched
        self._db.flush()

        codes = self._reissue_recovery_codes(user.id)
        record_audit_event(
            self._db,
            actor_id=user.id,
            entity_type=MFA_AUDIT_ENTITY_TYPE,
            entity_id=user.id,
            action="mfa_enrolled",
            after={"recovery_codes_issued": len(codes)},
        )
        logger.info(
            "Operator MFA enrollment activated", extra={"user_id": str(user.id)}
        )
        return codes

    # Verification

    def check_login_second_factor(
        self, totp_code: str | None, recovery_code: str | None
    ) -> str | None:
        """Second-factor check during verify-otp (unauthenticated flow).

        Returns the ``mfa`` token-claim level: ``"enroll"`` when the
        operator has no ACTIVE enrollment (constrained enrollment-only
        tokens), ``"totp"`` on a valid TOTP or recovery proof, or ``None``
        on failure — the AUTH SERVICE raises the generic 401 so the
        response is byte-identical to a wrong password/OTP (enumeration
        safety). Deliberately writes no audit event: this path has no
        proven session and the trail must not become a probe target;
        the shared throttle bounds abuse.
        """
        user = self._current_user
        row = self.enrollment_row(user.id)
        if row is None or row.status != OperatorMfaStatus.ACTIVE:
            return "enroll"

        self._enforce_attempt_throttle(user.id)

        secret = decrypt_totp_secret(row.secret_encrypted)
        if secret and totp_code:
            matched = verify_totp(secret, totp_code, min_counter=row.last_used_counter)
            if matched is not None:
                row.last_used_counter = matched
                self._db.flush()
                return "totp"

        if recovery_code and self._burn_recovery_code(user.id, recovery_code):
            return "totp"

        return None

    def verify_step_up(self, code: str | None, *, action: str) -> None:
        """Demand a fresh second-factor proof for one destructive action.

        Accepts a live TOTP code (replay-fenced, drift-windowed) or a
        single-use recovery code. Both the grant and the denial are
        audited and COMMITTED immediately: the replay fence and the
        evidence must survive regardless of what the guarded action does
        afterwards. Fails closed when no ACTIVE enrollment exists (a full
        operator token should be impossible without one, but a stale
        pre-reset token must not become a bypass).
        """
        user = self._current_user
        self._enforce_attempt_throttle(user.id)

        row = self.enrollment_row(user.id)
        method: str | None = None
        if row is not None and row.status == OperatorMfaStatus.ACTIVE and code:
            secret = decrypt_totp_secret(row.secret_encrypted)
            if secret:
                matched = verify_totp(secret, code, min_counter=row.last_used_counter)
                if matched is not None:
                    row.last_used_counter = matched
                    method = "totp"
            if (
                method is None
                and looks_like_recovery_code(code)
                and self._burn_recovery_code(user.id, code)
            ):
                method = "recovery_code"

        if method is None:
            record_audit_event(
                self._db,
                actor_id=user.id,
                entity_type=MFA_AUDIT_ENTITY_TYPE,
                entity_id=user.id,
                action="step_up_denied",
                after={"action": action},
            )
            self._db.commit()
            logger.warning(
                "Operator step-up denied",
                extra={"user_id": str(user.id), "action": action},
            )
            raise UnauthorizedException(_STEP_UP_ERROR)

        record_audit_event(
            self._db,
            actor_id=user.id,
            entity_type=MFA_AUDIT_ENTITY_TYPE,
            entity_id=user.id,
            action="step_up_granted",
            after={"action": action, "method": method},
        )
        self._db.commit()

    # Private helpers

    def _unused_recovery_codes(self, user_id: uuid.UUID):
        return self._db.query(OperatorRecoveryCode).filter(
            OperatorRecoveryCode.user_id == user_id,
            OperatorRecoveryCode.used_at.is_(None),
        )

    def _reissue_recovery_codes(self, user_id: uuid.UUID) -> list[str]:
        """Invalidate any existing codes and persist a fresh hashed set."""
        self._db.query(OperatorRecoveryCode).filter(
            OperatorRecoveryCode.user_id == user_id
        ).delete(synchronize_session=False)
        codes = generate_recovery_codes()
        for code in codes:
            self._db.add(
                OperatorRecoveryCode(
                    user_id=user_id, code_hash=hash_recovery_code(code)
                )
            )
        self._db.flush()
        return codes

    def _burn_recovery_code(self, user_id: uuid.UUID, code: str) -> bool:
        """Consume a recovery code; True iff it matched an unused row.

        Single-use is enforced by filtering on ``used_at IS NULL`` and
        stamping ``used_at`` in the same transaction. Usage is audited
        (success-path write, flushed with the surrounding transaction).
        """
        row = (
            self._unused_recovery_codes(user_id)
            .filter(OperatorRecoveryCode.code_hash == hash_recovery_code(code))
            .first()
        )
        if row is None:
            return False
        row.used_at = datetime.now(UTC)
        self._db.flush()
        remaining = self._unused_recovery_codes(user_id).count()
        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type=MFA_AUDIT_ENTITY_TYPE,
            entity_id=user_id,
            action="mfa_recovery_code_used",
            after={"remaining": remaining},
        )
        logger.info(
            "Operator MFA recovery code used",
            extra={"user_id": str(user_id), "remaining": remaining},
        )
        return True

    def _enforce_attempt_throttle(self, user_id: uuid.UUID) -> None:
        """Throttle second-factor attempts per operator (shared store).

        Reuses the auth throttle store (same pluggable backend and
        fail-open-on-outage semantics as the login throttle — the second
        factor itself still gates).
        """
        from app.modules.auth.service import _auth_throttle_store

        result = _auth_throttle_store().hit(
            f"mfa:{user_id}",
            settings.MFA_MAX_ATTEMPTS,
            settings.MFA_ATTEMPT_WINDOW_SECONDS,
        )
        if not result.allowed:
            logger.warning(
                "Operator MFA attempts throttled", extra={"user_id": str(user_id)}
            )
            raise RateLimitException(
                detail="Too many attempts. Please try again later.",
                retry_after=result.retry_after,
            )
