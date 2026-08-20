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
    remaining lifetime so revoked ids expire themselves. ``extra`` claims
    (e.g. the operator ``mfa`` level, ADR-0014) are carried so a refresh can
    propagate them — never invent or upgrade them.
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
        return payload
    except JWTError as e:
        raise UnauthorizedException("Invalid or expired token.") from e


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
