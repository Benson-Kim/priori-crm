import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.common.exceptions import UnauthorizedException
from app.lib.config import settings

# auto_error=False so a missing/malformed Authorization header surfaces as our
# own UnauthorizedException (401 in the app envelope) instead of FastAPI's bare
# HTTPException 403.
bearer_scheme = HTTPBearer(auto_error=False)


def internal_secret_configured() -> bool:
    """Whether the internal (machine-to-machine) surface is enabled at all.

    Empty means disabled: the internal endpoints fail closed until a real
    secret is configured.
    """
    return bool(settings.INTERNAL_API_SECRET or settings.INTERNAL_API_SECRET_NEXT)


def matches_internal_secret(supplied: str | None) -> bool:
    """Constant-time check of an X-Internal-Secret value.

    Single source of truth for BOTH consumers of the header — the service-
    principal classification (``_is_verified_service_caller``) and the
    internal-endpoint gate (``verify_internal_secret``) — so the two can
    never drift apart (#67 line review §1c).

    ``INTERNAL_API_SECRET_NEXT`` is accepted alongside ``INTERNAL_API_SECRET``
    to allow zero-downtime rotation: configure the new secret in _NEXT, roll
    the callers, promote it, clear _NEXT. Empty configured values NEVER
    match (fail closed), and both comparisons always run so timing does not
    reveal which slot matched.
    """
    if not supplied:
        return False
    matched = False
    for expected in (settings.INTERNAL_API_SECRET, settings.INTERNAL_API_SECRET_NEXT):
        if expected and secrets.compare_digest(supplied, expected):
            matched = True
    return matched


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def create_access_token(subject: str, extra: dict | None = None) -> str:
    """Create a JWT access token."""
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": subject, "exp": expire, "type": "access"}
    if extra:
        payload.update(extra)
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def create_refresh_token(
    subject: str, extra: dict | None = None
) -> tuple[str, str, datetime]:
    """Create a JWT refresh token.

    Returns ``(token, jti, expires_at)``. The ``jti`` (unique token id) lets
    the auth service revoke this exact token via the denylist, and
    ``expires_at`` lets the caller size the denylist entry's TTL to the token's
    remaining lifetime so revoked ids expire themselves. ``extra`` carries
    additional claims (e.g. the ``sid`` session id, issue #67) so rotation
    can keep a session's identity across token pairs.
    """
    expire = datetime.now(UTC) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    jti = str(uuid.uuid4())
    payload = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(UTC),
        "type": "refresh",
        "jti": jti,
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return token, jti, expire


def decode_access_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Decode and validate a JWT access token from the Authorization header."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedException("Authentication required.")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("type") != "access":
            raise UnauthorizedException("Invalid token type.")
    except JWTError as e:
        raise UnauthorizedException("Invalid or expired token.") from e

    # Terminated-session revocation (#67 review F5): when a session dies
    # (risk terminate, refresh-reuse detection, logout, password reset),
    # its sid is pushed onto the shared denylist so already-issued access
    # tokens die IMMEDIATELY — not at natural expiry. This runs on the
    # access-token validation path itself, so it holds even where the
    # zero-trust gate's per-request session re-check does not (e.g.
    # ABAC_ENABLED=false). Legacy tokens without a sid have no session to
    # have terminated.
    sid = payload.get("sid")
    if sid is not None:
        from app.common.token_denylist import is_session_access_revoked

        if is_session_access_revoked(str(sid)):
            raise UnauthorizedException("Session terminated.")
    return payload


def decode_refresh_token(token: str) -> dict:
    """Decode and validate a JWT refresh token."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != "refresh":
            raise UnauthorizedException("Invalid token type.")
        return payload
    except JWTError as e:
        raise UnauthorizedException("Invalid or expired refresh token.") from e
