import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.common.exceptions import (
    BadRequestException,
    RateLimitException,
    UnauthorizedException,
)
from app.common.rate_limit_store import RateLimitStore, build_rate_limit_store
from app.common.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    verify_password,
)
from app.common.token_denylist import TokenDenylist, build_token_denylist
from app.lib.config import settings
from app.lib.email import email_service
from app.modules.auth.models import OTPCode, User

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 5

# Single generic auth-failure message. Login and OTP verification all surface
# the same text so the API never reveals whether an email exists, a password
# was wrong, or an OTP was wrong/expired/exhausted.
_GENERIC_AUTH_ERROR = "Invalid email or code."


@lru_cache(maxsize=1)
def _auth_throttle_store() -> RateLimitStore:
    """Shared counter store for auth-attempt throttling.

    Reuses the same pluggable backend as the request rate limiter so a
    multi-worker / multi-instance deployment shares one window (Redis) and
    local/dev stays in-process. Cached so all AuthService instances within a
    process share a single counter.
    """
    return build_rate_limit_store(
        backend=settings.RATE_LIMIT_BACKEND,
        redis_url=settings.REDIS_URL,
    )


@lru_cache(maxsize=1)
def _refresh_token_denylist() -> TokenDenylist:
    """Shared denylist store for revoked refresh tokens.

    Mirrors the throttle store: a pluggable backend (in-memory for dev /
    single-worker, Redis for a shared multi-instance denylist) built from the
    TOKEN_DENYLIST_BACKEND / REDIS_URL settings. Cached so all AuthService
    instances within a process share one denylist.
    """
    return build_token_denylist(
        backend=settings.TOKEN_DENYLIST_BACKEND,
        redis_url=settings.REDIS_URL,
    )


class AuthService:
    """Handles authentication business logic"""

    def __init__(self, db: Session) -> None:
        self._db = db

    # Login (Step 1)

    def login(self, email: str, password: str) -> None:
        """Validate credentials and send an OTP code.

        Returns nothing: the masked email is intentionally not surfaced to the
        caller. All failure modes raise the same generic 401 so the
        endpoint cannot be used to enumerate accounts.
        """
        self._enforce_attempt_throttle(email)

        user = self._get_user_by_email(email)
        if user is None:
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        if not verify_password(password, user.password_hash):
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        if not user.is_active:
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        otp_code = self._create_otp(user)
        self._send_otp_email(user.email, otp_code)

    # Verify OTP (Step 2)

    def verify_otp(self, email: str, code: str) -> tuple[str, str, User]:
        """Verify OTP and return (access_token, refresh_token, user)."""
        self._enforce_attempt_throttle(email)

        user = self._get_user_by_email(email)
        if user is None:
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        # Find the latest live OTP for the user regardless of the submitted
        # code, so a wrong guess is counted against that code's attempt budget
        # rather than silently ignored.
        otp = (
            self._db.query(OTPCode)
            .filter(
                OTPCode.user_id == user.id,
                OTPCode.is_used.is_(False),
            )
            .order_by(OTPCode.created_at.desc())
            .first()
        )

        # Uniform failure for both "no live code" and "expired" so neither the
        # account state nor the code state leaks
        if otp is None or otp.is_expired:
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        if otp.code != code:
            # Count the failed attempt against this code's budget; consume the
            # code once the cap is reached so it can no longer be brute-forced
            # . This path must COMMIT explicitly (not just flush):
            # the verify then raises a 401, and get_db() rolls back the
            # request-scoped transaction on a propagating exception,
            # which would otherwise discard the increment and make the cap
            # un-enforceable. The success path below only flush()es and lets
            # get_db() own the commit.
            otp.attempt_count += 1
            if otp.attempt_count >= settings.AUTH_MAX_OTP_ATTEMPTS:
                otp.is_used = True
            self._db.commit()
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        # Mark OTP as used and invalidate all other unused OTPs for this user.
        # Both writes flush within the request-scoped transaction; the commit
        # is owned by get_db() once the request completes
        otp.is_used = True
        self._db.flush()

        self._invalidate_pending_otps(user.id, exclude_id=otp.id)

        access_token = create_access_token(subject=str(user.id))
        refresh_token, _jti, _exp = create_refresh_token(subject=str(user.id))

        return access_token, refresh_token, user

    # Refresh Token

    def refresh_access_token(self, refresh_token: str) -> tuple[str, str]:
        """Validate a refresh token and rotate it.

        Returns ``(access_token, new_refresh_token)``. The presented refresh
        token is revoked (added to the denylist for its remaining lifetime) and
        a fresh refresh token is issued, so a leaked/stolen token is usable at
        most once before rotation invalidates it.
        """
        payload = decode_refresh_token(refresh_token)
        user_id = payload.get("sub")
        jti = payload.get("jti")

        if jti is not None and _refresh_token_denylist().is_revoked(jti):
            raise UnauthorizedException("Refresh token has been revoked.")

        user = self._db.query(User).filter(User.id == user_id).first()
        if user is None or not user.is_active:
            raise UnauthorizedException("Invalid or inactive user.")

        # Rotate: revoke the presented token, then mint a new pair.
        self._revoke_refresh_payload(payload)

        access_token = create_access_token(subject=str(user.id))
        new_refresh_token, _jti, _exp = create_refresh_token(subject=str(user.id))

        return access_token, new_refresh_token

    # Logout

    def logout(self, refresh_token: str) -> None:
        """Revoke a refresh token on logout.

        Idempotent and tolerant of a missing/invalid token: an unparseable or
        already-expired token is simply a no-op so logout never errors.
        """
        try:
            payload = decode_refresh_token(refresh_token)
        except UnauthorizedException:
            return

        self._revoke_refresh_payload(payload)

    # Maintenance

    def purge_expired_otps(self, retention_minutes: int = OTP_EXPIRY_MINUTES) -> int:
        """Delete used or expired OTP rows past the retention window.

        Keeps the otp_codes table bounded. Rows are eligible once they are
        either used or expired *and* older than ``retention_minutes`` (a small
        grace period so an in-flight verify is never raced). Returns the number
        of rows deleted.

        This commits explicitly because it is invoked outside a request (by the
        scheduler/internal endpoint) and is the sole owner of its transaction.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=retention_minutes)
        deleted = (
            self._db.query(OTPCode)
            .filter(
                OTPCode.created_at < cutoff,
                or_(
                    OTPCode.is_used.is_(True),
                    OTPCode.expires_at < datetime.now(UTC),
                ),
            )
            .delete(synchronize_session=False)
        )
        self._db.commit()
        logger.info("Purged %d expired/used OTP rows", deleted)
        return deleted

    # Private Helpers

    @staticmethod
    def _revoke_refresh_payload(payload: dict) -> None:
        """Add a refresh token's jti to the denylist for its remaining life.

        Sizing the TTL to the token's own ``exp`` means the denylist entry
        expires itself once the token would have expired anyway, so the store
        stays bounded by the live-token window. A payload without a jti (legacy
        token) or already past expiry is a no-op.
        """
        jti = payload.get("jti")
        if jti is None:
            return
        exp = payload.get("exp")

        now = int(datetime.now(UTC).timestamp())
        ttl = int(exp) - now if exp is not None else 0
        if ttl <= 0:
            return
        _refresh_token_denylist().revoke(jti, ttl)

    def _enforce_attempt_throttle(self, email: str) -> None:
        """Throttle login/verify attempts per identifier.

        Keyed by a hash of the lowercased email so the raw address is never
        used as a cache key. Counting is delegated to the shared store, which
        fails open on backend errors, so a cache outage degrades to "no
        throttle" rather than locking everyone out.
        """
        identifier = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        result = _auth_throttle_store().hit(
            f"auth:{identifier}",
            settings.AUTH_LOGIN_MAX_ATTEMPTS,
            settings.AUTH_LOGIN_WINDOW_SECONDS,
        )
        if not result.allowed:
            logger.warning("Auth attempt throttled for identifier %s", identifier)
            raise RateLimitException(
                detail="Too many attempts. Please try again later.",
                retry_after=result.retry_after,
            )

    def _get_user_by_email(self, email: str) -> User | None:
        """Look up a user by email address."""
        return self._db.query(User).filter(User.email == email).first()

    def _create_otp(self, user: User) -> str:
        """Generate a 6-digit OTP, persist it, and return the code string."""
        # Invalidate any pending OTPs first
        self._invalidate_pending_otps(user.id)

        code = "".join(secrets.choice("0123456789") for _ in range(6))
        otp = OTPCode(
            user_id=user.id,
            code=code,
            expires_at=datetime.now(UTC) + timedelta(minutes=OTP_EXPIRY_MINUTES),
        )
        self._db.add(otp)
        self._db.flush()
        logger.info("OTP created for user %s", user.email)
        return code

    def _invalidate_pending_otps(self, user_id, exclude_id=None) -> None:
        """Mark all unused OTPs for a user as used."""
        query = self._db.query(OTPCode).filter(
            OTPCode.user_id == user_id,
            OTPCode.is_used.is_(False),
        )
        if exclude_id:
            query = query.filter(OTPCode.id != exclude_id)
        query.update({"is_used": True}, synchronize_session="fetch")
        self._db.flush()

    def _send_otp_email(self, recipient: str, otp_code: str) -> None:
        """Send OTP via email. Log-only in development if SES is not configured."""
        if settings.ENVIRONMENT == "development" and not settings.AWS_ACCESS_KEY_ID:
            logger.warning(
                "DEV MODE - OTP for %s: %s (email not sent, SES not configured)",
                recipient,
                otp_code,
            )
            return

        sent = email_service.send_otp(recipient, otp_code)
        if not sent:
            raise BadRequestException("Failed to send verification email. Try again.")
