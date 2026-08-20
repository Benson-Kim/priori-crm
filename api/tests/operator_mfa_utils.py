"""Shared operator-MFA test helpers (ADR-0014, issue #73).

Since operator MFA landed, a bare ``create_access_token(subject=...)``
token is CONSTRAINED for platform operators (no ``mfa`` claim ⇒
enrollment-only surface), and the destructive platform PATCH routes
demand a fresh ``X-MFA-Code`` step-up proof. These helpers give platform
tests a fully-enrolled operator, a full-console token and a live code in
one call, without every suite re-implementing the crypto plumbing.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import object_session

from app.common.mfa import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_totp_secret,
    totp_at,
)
from app.common.security import create_access_token
from app.constants.enums import OperatorMfaStatus, UserRole
from app.modules.platform.models import OperatorMfaTotp


def ensure_enrolled(db, user) -> str:
    """Give the operator an ACTIVE TOTP enrollment; return the base32 secret.

    Idempotent (reuses/decrypts an existing row). Resets the replay fence
    every call so sequential test requests inside one 30-second TOTP step
    can each present the current code — replay behaviour itself is pinned
    explicitly in test_operator_mfa.py, not here.
    """
    row = db.query(OperatorMfaTotp).filter(OperatorMfaTotp.user_id == user.id).first()
    if row is None:
        secret = generate_totp_secret()
        row = OperatorMfaTotp(
            user_id=user.id,
            secret_encrypted=encrypt_totp_secret(secret),
            status=OperatorMfaStatus.ACTIVE.value,
            confirmed_at=datetime.now(UTC),
        )
        db.add(row)
    else:
        secret = decrypt_totp_secret(row.secret_encrypted)
        row.status = OperatorMfaStatus.ACTIVE.value
    row.last_used_counter = None
    db.commit()
    return secret


def auth_headers(user) -> dict:
    """Authorization headers (+ fresh X-MFA-Code for operators) for any user.

    Tenant users get a plain access token. Platform operators get a
    full-console token (``mfa: "totp"``), an ACTIVE enrollment and a live
    step-up code, so both reads and destructive platform writes work.
    Recompute per request: each call resets the replay fence and mints a
    code for the current step.
    """
    if user.role != UserRole.PLATFORM_OPERATOR.value:
        return {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}
    db = object_session(user)
    secret = ensure_enrolled(db, user)
    token = create_access_token(subject=str(user.id), extra={"mfa": "totp"})
    return {"Authorization": f"Bearer {token}", "X-MFA-Code": totp_at(secret)}
