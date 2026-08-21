import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.exceptions import (
    BadRequestException,
    RateLimitException,
    StepUpRequiredException,
    UnauthorizedException,
)
from app.common.rate_limit_store import RateLimitStore, build_rate_limit_store
from app.common.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.common.token_denylist import TokenDenylist, build_token_denylist
from app.constants.enums import SessionStatus
from app.lib.config import settings
from app.lib.email import email_service
from app.modules.auth.models import OTPCode, PasswordResetToken, User, UserSession

if TYPE_CHECKING:
    from app.common.authz.context import AccessContext

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 5

# Single generic auth-failure message. Login and OTP verification all surface
# the same text so the API never reveals whether an email exists, a password
# was wrong, or an OTP was wrong/expired/exhausted.
_GENERIC_AUTH_ERROR = "Invalid email or code."

# Single generic password-reset failure message. Unknown / wrong / expired /
# already-used tokens all surface this same text so the endpoint never reveals
# token or account state.
_GENERIC_RESET_ERROR = "This password reset link is invalid or has expired."

PASSWORD_RESET_TOKEN_BYTES = 32


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

    def resend_otp(self, email: str) -> None:
        """Resend an OTP code, but only once the previous one has expired.

        A resend may only mint and email a new code after the full
        OTP_EXPIRY_MINUTES window has elapsed since the last code was sent.
        While a live (unexpired, unused) code still exists the request is a
        silent no-op: the existing code stays valid and no new email is sent.
        This stops a caller from spamming OTP emails and from invalidating
        their own in-flight code by resending early. It mirrors the
        enumeration-safe contract of the rest of the flow — every outcome is
        indistinguishable to the caller (the router always returns the same
        generic message), so it never reveals account or code state.
        """
        self._enforce_attempt_throttle(email)

        user = self._get_user_by_email(email)
        if user is None:
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        if not user.is_active:
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        # Find the latest live (unused) code. If it has not yet expired, the
        # 5-minute window has not elapsed since it was sent, so honour the
        # gate and send nothing rather than re-issuing early.
        latest_otp = (
            self._db.query(OTPCode)
            .filter(
                OTPCode.user_id == user.id,
                OTPCode.is_used.is_(False),
            )
            .order_by(OTPCode.created_at.desc())
            .first()
        )
        if latest_otp is not None and not latest_otp.is_expired:
            logger.info(
                "Resend OTP suppressed for user %s: live code not yet expired",
                user.email,
            )
            return

        otp_code = self._create_otp(user)
        self._send_otp_email(user.email, otp_code)

    # Verify OTP (Step 2)

    def verify_otp(
        self, email: str, code: str, context: "AccessContext | None" = None
    ) -> tuple[str, str, User]:
        """Verify OTP and return (access_token, refresh_token, user).

        ``context`` is the zero-trust gate's per-request ``AccessContext``
        (#67), threaded through by the router: success absorbs it into the
        user's behavioural baseline (a passed step-up folds the triggering
        soft signals in, so the same new device/place never re-challenges);
        exhausting the attempt budget terminates the user's challenged
        sessions (repeated failed step-ups are a HARD signal).
        """
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

        if otp.code != self._hash_otp(code):
            # Count the failed attempt against this code's budget; consume the
            # code once the cap is reached so it can no longer be brute-forced
            # . This path must COMMIT explicitly (not just flush):
            # the verify then raises a 401, and get_db() rolls back the
            # request-scoped transaction on a propagating exception,
            # which would otherwise discard the increment and make the cap
            # un-enforceable. The success path below only flush()es and lets
            # CommitOnSuccessRoute (app/common/routing.py) own the commit;
            # get_db()'s teardown commit is a safety net only.
            otp.attempt_count += 1
            if otp.attempt_count >= settings.AUTH_MAX_OTP_ATTEMPTS:
                otp.is_used = True
                # Repeated failed step-ups are a HARD signal (#67): whoever
                # is burning the OTP budget cannot read the inbox — the
                # challenged sessions they are trying to launder die now
                # and only a full, successful re-auth restores access. A
                # legitimate user's challenged session was already unusable
                # (only re-auth clears it), so this locks out no one.
                # Committed with the attempt counter below, durably past
                # the 401 this raise causes.
                self._terminate_user_sessions(
                    user.id,
                    "failed_step_up",
                    statuses=(SessionStatus.CHALLENGE_REQUIRED.value,),
                )
            self._db.commit()
            raise UnauthorizedException(_GENERIC_AUTH_ERROR)

        # Mark OTP as used and invalidate all other unused OTPs for this user.
        # Both writes flush within the request-scoped transaction; the commit
        # is owned by CommitOnSuccessRoute (app/common/routing.py), which
        # commits before the response is sent; get_db()'s teardown commit
        # is a safety net only.
        otp.is_used = True
        self._db.flush()

        self._invalidate_pending_otps(user.id, exclude_id=otp.id)

        # Continuous session risk (#67): every completed OTP verification
        # mints a FRESH session whose id travels as the `sid` claim in both
        # tokens. This is also how a step-up challenge is satisfied: the
        # challenged session stays challenged forever; re-authenticating
        # yields a new, cleanly-scored session.
        # `sua` ("stepped-up-at") records that this instant is backed by a
        # completed OTP. The static ABAC rules (off-hours, unknown-geo) read
        # it so their CHALLENGE is answerable: without it those rules depend
        # only on the wall clock and the path, which no amount of
        # re-authenticating can change, turning every 401 into a lockout for
        # the whole window. It rides in BOTH tokens so a rotation can carry
        # it forward unchanged.
        session = self._create_session(user)
        claims = {
            "sid": str(session.id),
            "sua": int(datetime.now(UTC).timestamp()),
        }

        # Baseline absorption (#67): a completed OTP proves inbox control
        # from THIS device, at THIS place, at THIS hour — fold the context
        # into the user's behavioural baseline so the same new device/place
        # never re-fires as a soft signal (no repeated challenges for a
        # context the user has already verified). Rides this request's
        # transaction, atomic with the session mint.
        from app.common.authz.baselines import absorb_context

        absorb_context(self._db, user.id, context)

        access_token = create_access_token(subject=str(user.id), extra=claims)
        refresh_token, _jti, _exp = create_refresh_token(
            subject=str(user.id), extra=claims
        )

        return access_token, refresh_token, user

    # Password reset (forgot-password flow)

    def request_password_reset(self, email: str) -> None:
        """Issue a password-reset token and email a reset link.

        Enumeration-safe: returns None in every case. A non-existent or
        inactive account simply does no work; the router responds with the
        same generic message either way, so this endpoint can never reveal
        whether an address has an account.

        For a valid, active account it invalidates any pending reset tokens,
        creates a fresh single-use token (only the SHA-256 digest is stored)
        and emails the reset link. The plaintext token exists only in the
        outgoing email.
        """
        self._enforce_attempt_throttle(email)

        user = self._get_user_by_email(email)
        if user is None or not user.is_active:
            # No such (active) account: do nothing, reveal nothing.
            return

        raw_token = self._create_password_reset_token(user)
        self._send_password_reset_email(user.email, raw_token)

    def reset_password(self, raw_token: str, new_password: str) -> None:
        """Complete a password reset given a token and a new password.

        The token is matched by its SHA-256 digest. Wrong / expired / used /
        unknown tokens all fail with the same generic error so nothing about
        the token or account state leaks. Brute-force resistance comes from the
        token's 256-bit entropy (secrets.token_urlsafe(32)) — a wrong guess
        simply matches no row — backed by the per-token-digest attempt
        throttle; unlike the 6-digit OTP, there is no low-entropy secret to
        attempt-count per row.

        On success: the new bcrypt password hash is stored, the token is
        marked used, any other pending reset tokens are invalidated, and the
        user's entire refresh-token family is revoked so a password reset
        terminates every existing session (defence against an attacker who
        already holds a session for the account).
        """
        token_hash = self._hash_token(raw_token)

        # Throttle by the token digest so a stolen-link brute force is bounded
        # without needing the (unknown) email here.
        self._enforce_attempt_throttle(token_hash)

        # Resolve the token row directly from its digest. A matching row is
        # required even to know which user this is, so an unknown token is
        # indistinguishable from a wrong one.
        reset = (
            self._db.query(PasswordResetToken)
            .filter(PasswordResetToken.token == token_hash)
            .order_by(PasswordResetToken.created_at.desc())
            .first()
        )

        if reset is None or reset.is_used or reset.is_expired:
            raise UnauthorizedException(_GENERIC_RESET_ERROR)

        user = self._db.query(User).filter(User.id == reset.user_id).first()
        if user is None or not user.is_active:
            raise UnauthorizedException(_GENERIC_RESET_ERROR)

        # Apply the new password and burn the token, all within the
        # request-scoped transaction; CommitOnSuccessRoute
        # (app/common/routing.py) owns the commit on success — get_db()'s
        # teardown commit is a safety net only.
        user.password_hash = hash_password(new_password)
        reset.is_used = True
        self._db.flush()

        self._invalidate_pending_reset_tokens(user.id, exclude_id=reset.id)

        # A password reset must terminate every existing session: fence the
        # user's whole refresh-token family so any outstanding refresh token
        # (including an attacker's) is rejected. The user signs in afresh.
        self._revoke_token_family(str(user.id))
        # The fence only reaches REFRESH tokens. An attacker's already-issued
        # ACCESS token stays valid for up to JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        # after the victim rotates their password — the window the reset
        # exists to close. Terminating the session rows shuts it now, because
        # the zero-trust gate re-checks session status on every request (#67).
        self._terminate_user_sessions(user.id, "password_reset")

        logger.info("Password reset completed for user %s", user.email)

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
            # Reuse of a rotated/revoked token is theft evidence:
            # the thief and the victim each hold a copy and one of them is
            # presenting a token that has already been spent. Fence the whole
            # family so the attacker's descendant chain dies too; the
            # legitimate user re-authenticates and mints a post-fence token.
            self._revoke_token_family(user_id)
            logger.warning(
                "Refresh-token reuse detected; revoked token family",
                extra={"user_id": str(user_id)},
            )
            raise UnauthorizedException("Refresh token has been revoked.")

        # Family fence: reject tokens minted at or before the most recent
        # reuse event, regardless of their individual jti.
        iat = payload.get("iat")
        fence = _refresh_token_denylist().get_fence(f"user:{user_id}")
        if fence is not None and iat is not None and float(iat) <= fence:
            raise UnauthorizedException("Refresh token has been revoked.")

        user = self._db.query(User).filter(User.id == user_id).first()
        if user is None or not user.is_active:
            raise UnauthorizedException("Invalid or inactive user.")

        # Continuous session risk (#67): a refresh never launders session
        # state. A terminated session's tokens are dead, and a challenged
        # session cannot be rotated back to life — the caller must re-run
        # the full login → OTP flow, which mints a fresh session.
        sid = payload.get("sid")
        session = self._get_session(sid)
        if sid is not None and session is None:
            raise UnauthorizedException("Refresh token has been revoked.")
        if session is not None:
            if session.status == SessionStatus.TERMINATED.value:
                raise UnauthorizedException("Refresh token has been revoked.")
            if session.status == SessionStatus.CHALLENGE_REQUIRED.value:
                raise StepUpRequiredException(
                    detail=(
                        "Additional verification is required. Please sign in "
                        "again to receive a verification code."
                    )
                )

        # Rotate: atomically spend the presented token BEFORE minting the
        # new pair. revoke_if_new is a single revoke-and-report operation,
        # so exactly one concurrent presenter of a given jti can win this
        # step. Any other concurrent presenter lands here because the
        # pre-check above passed for both — the same theft evidence the
        # pre-check catches for sequential requests — so it takes the same
        # family-fence + 401 path and no second descendant chain is minted.
        if not self._revoke_refresh_payload(payload):
            self._revoke_token_family(user_id)
            logger.warning(
                "Concurrent refresh-token reuse detected; revoked token family",
                extra={"user_id": str(user_id)},
            )
            raise UnauthorizedException("Refresh token has been revoked.")

        # Carry the session claims across the rotation UNCHANGED (#67).
        #
        # `sid`: without this the rotated pair carries no session at all, so
        # continuous risk scoring silently stops applying to a session after
        # its first refresh — the feature would expire with the original
        # access token.
        #
        # `sua`: copied, never re-stamped. Re-stamping would let anyone
        # holding a stolen refresh token launder an indefinite step-up
        # without ever proving an OTP, which is precisely what the claim
        # exists to prove. For the same reason the step-up rules cannot lean
        # on `iat`, which rotation resets by design.
        claims = {k: payload[k] for k in ("sid", "sua") if payload.get(k) is not None}

        access_token = create_access_token(subject=str(user.id), extra=claims)
        new_refresh_token, _jti, _exp = create_refresh_token(
            subject=str(user.id), extra=claims
        )

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

        # Continuous session risk (#67): logout kills the whole session, so
        # any still-live ACCESS token carrying this `sid` dies with it —
        # zero trust, not just refresh-token revocation.
        session = self._get_session(payload.get("sid"))
        if session is not None and session.status != SessionStatus.TERMINATED.value:
            session.status = SessionStatus.TERMINATED.value
            session.termination_reason = "logout"
            record_audit_event(
                self._db,
                actor_id=session.user_id,
                entity_type="session",
                entity_id=session.id,
                action="session_terminated",
                after={"why": "logout"},
            )
            self._db.flush()

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
        reset_deleted = (
            self._db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.created_at < cutoff,
                or_(
                    PasswordResetToken.is_used.is_(True),
                    PasswordResetToken.expires_at < datetime.now(UTC),
                ),
            )
            .delete(synchronize_session=False)
        )
        self._db.commit()
        logger.info(
            "Purged %d expired/used OTP rows and %d reset-token rows",
            deleted,
            reset_deleted,
        )
        return deleted + reset_deleted

    # Private Helpers

    @staticmethod
    def _revoke_token_family(user_id: str | None) -> None:
        """Fence every outstanding refresh token for a user.

        Writes a per-user fence timestamp with a TTL equal to the refresh
        lifetime, so all tokens minted up to now are rejected while tokens
        from a subsequent fresh login (later ``iat``) pass. The entry
        self-expires once every pre-fence token would be expired anyway.
        """
        if user_id is None:
            return
        import time

        ttl = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        _refresh_token_denylist().set_fence(f"user:{user_id}", time.time(), ttl)

    def _create_session(self, user: User) -> UserSession:
        """Mint a fresh, cleanly-scored session row (#67).

        Flush-only: the row commits with the verify-otp request via
        ``CommitOnSuccessRoute``, atomically with the OTP consumption.
        Context fields (ip, geo, device) start empty and are populated by
        the zero-trust gate on the session's first scored request.
        """
        session = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value)
        self._db.add(session)
        self._db.flush()
        logger.info("Session %s created for user %s", session.id, user.email)
        return session

    def _get_session(self, sid: str | None) -> UserSession | None:
        """Resolve a `sid` claim to its session row (None for legacy tokens)."""
        if not sid:
            return None
        try:
            session_id = uuid.UUID(str(sid))
        except ValueError:
            return None
        return self._db.get(UserSession, session_id)

    def _terminate_user_sessions(
        self, user_id, reason: str, statuses: tuple[str, ...] | None = None
    ) -> None:
        """Terminate a user's sessions, audited (#67).

        By default every non-terminated session dies (password reset).
        ``statuses`` narrows the sweep — the failed-step-up path kills only
        CHALLENGE_REQUIRED sessions, so an attacker burning the OTP budget
        cannot use it to knock out the victim's healthy active sessions.
        """
        query = self._db.query(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.status != SessionStatus.TERMINATED.value,
        )
        if statuses is not None:
            query = query.filter(UserSession.status.in_(statuses))
        sessions = query.all()
        for session in sessions:
            session.status = SessionStatus.TERMINATED.value
            session.termination_reason = reason
            record_audit_event(
                self._db,
                actor_id=session.user_id,
                entity_type="session",
                entity_id=session.id,
                action="session_terminated",
                after={"why": reason},
            )
        if sessions:
            self._db.flush()

    @staticmethod
    def _hash_otp(code: str) -> str:
        """SHA-256 digest of an OTP code (codes are never stored plaintext)."""
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        """SHA-256 digest of a reset token (tokens are never stored plaintext).

        The token is a high-entropy URL-safe random string, so a fast hash
        (SHA-256) is appropriate: there is nothing to brute force the way
        there is for a human password.
        """
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def _create_password_reset_token(self, user: User) -> str:
        """Generate a single-use reset token, persist its digest, return the raw.

        Any pending reset tokens for the user are invalidated first so only
        the newest link is ever live. Only the SHA-256 digest is stored; the
        returned plaintext is placed solely in the outgoing email link.
        """
        self._invalidate_pending_reset_tokens(user.id)

        raw_token = secrets.token_urlsafe(PASSWORD_RESET_TOKEN_BYTES)
        reset = PasswordResetToken(
            user_id=user.id,
            token=self._hash_token(raw_token),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES),
        )
        self._db.add(reset)
        self._db.flush()
        logger.info("Password reset token created for user %s", user.email)
        return raw_token

    def _invalidate_pending_reset_tokens(self, user_id, exclude_id=None) -> None:
        """Mark all unused reset tokens for a user as used."""
        query = self._db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.is_used.is_(False),
        )
        if exclude_id:
            query = query.filter(PasswordResetToken.id != exclude_id)
        query.update({"is_used": True}, synchronize_session="fetch")
        self._db.flush()

    def _send_password_reset_email(self, recipient: str, raw_token: str) -> None:
        """Email the password-reset link. Log-only in dev without SES config."""
        from urllib.parse import quote

        reset_link = (
            f"{settings.FRONTEND_BASE_URL.rstrip('/')}"
            f"/reset-password?token={quote(raw_token, safe='')}"
        )

        if settings.ENVIRONMENT == "development" and not settings.AWS_ACCESS_KEY_ID:
            logger.warning(
                "DEV MODE - password reset link for %s: %s (email not sent, "
                "SES not configured)",
                recipient,
                reset_link,
            )
            return

        try:
            email_service.send_password_reset(recipient, reset_link)
        except Exception as exc:  # delivery failures must not leak account state
            # Log, but do not surface a different response: the caller already
            # returns the generic message, so a send failure cannot be used to
            # probe which addresses exist.
            logger.error(
                "Failed to send password reset email",
                exc_info=exc,
                extra={"recipient": recipient},
            )

    @staticmethod
    def _revoke_refresh_payload(payload: dict) -> bool:
        """Atomically spend (revoke) a refresh token's jti.

        Single atomic revoke-and-report via ``TokenDenylist.revoke_if_new``:
        returns True when this call newly revoked the jti, False when it was
        already revoked — i.e. an earlier or concurrent presenter spent it
        first and the caller must treat the token as reused. ``logout``
        ignores the return value (revocation is idempotent);
        ``refresh_access_token`` branches on it to close the concurrent
        rotation window.

        Sizing the TTL to the token's own ``exp`` means the denylist entry
        expires itself once the token would have expired anyway, so the store
        stays bounded by the live-token window. A payload without a jti
        (legacy token) or already past expiry has nothing to spend and
        reports True — exp validation owns rejecting expired tokens.
        """
        jti = payload.get("jti")
        if jti is None:
            return True
        exp = payload.get("exp")

        now = int(datetime.now(UTC).timestamp())
        ttl = int(exp) - now if exp is not None else 0
        if ttl <= 0:
            return True
        return _refresh_token_denylist().revoke_if_new(jti, ttl)

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
        # Store only the digest: a DB read must never yield a
        # live login code. The plaintext exists only in the outgoing email.
        otp = OTPCode(
            user_id=user.id,
            code=self._hash_otp(code),
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
