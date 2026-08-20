"""Operator MFA primitives (ADR-0014).

Self-contained RFC 6238 TOTP (HMAC-SHA-1, 6 digits, 30-second steps —
pinned against the RFC's Appendix B test vectors in
tests/test_operator_mfa.py), otpauth provisioning-URI construction,
Fernet-based at-rest encryption of TOTP seeds, and single-use recovery-code
generation/hashing.

Deliberately stdlib for the TOTP math itself (``hmac``/``struct``) so no
third-party package sits on the authentication-critical path; the only
external dependency is ``cryptography`` (PyCA) for the at-rest encryption.

Secret-handling rules (ADR-0014): plaintext TOTP seeds exist only in the
enrollment response body — they are never logged, never passed via argv,
and only the Fernet ciphertext is persisted. Recovery codes are stored as
SHA-256 digests only (high-entropy random values, so a fast hash is
appropriate — same reasoning as password-reset tokens).
"""

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
from functools import lru_cache
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from app.lib.config import settings

#: RFC 6238 defaults, shared with every mainstream authenticator app.
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30

#: 160-bit seed, the RFC 4226 recommended key length for HMAC-SHA-1.
_SECRET_BYTES = 20

#: Recovery codes issued per activation; 80 bits of entropy each.
RECOVERY_CODE_COUNT = 10
_RECOVERY_CODE_BYTES = 10


# TOTP (RFC 6238)


def generate_totp_secret() -> str:
    """Return a fresh base32-encoded 160-bit TOTP seed."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii")


def hotp(secret_b32: str, counter: int, digits: int = TOTP_DIGITS) -> str:
    """RFC 4226 HOTP value for one counter, as a zero-padded string.

    HMAC-SHA-1 here is the algorithm RFC 6238 mandates for interoperable
    authenticator apps; this is a keyed-MAC use, not a collision-exposed
    signature use.
    """
    key = base64.b32decode(secret_b32, casefold=True)
    # HMAC-SHA-1 is a keyed-MAC use mandated by RFC 6238, not a
    # collision-exposed signature use.
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def totp_counter(at: float | None = None) -> int:
    """The TOTP time-step counter for an instant (default: now)."""
    moment = time.time() if at is None else at
    return int(moment // TOTP_PERIOD_SECONDS)


def totp_at(secret_b32: str, at: float | None = None) -> str:
    """The TOTP code valid at an instant (default: now). Test/tooling helper."""
    return hotp(secret_b32, totp_counter(at))


def verify_totp(
    secret_b32: str,
    code: str | None,
    *,
    at: float | None = None,
    drift_steps: int | None = None,
    min_counter: int | None = None,
) -> int | None:
    """Verify a TOTP code; return the matched time-step counter or ``None``.

    - ``drift_steps`` (default ``settings.MFA_TOTP_DRIFT_STEPS``): accept
      codes from ±N adjacent steps to absorb clock skew. A code outside the
      window is rejected — this is also the step-up "expiry".
    - ``min_counter``: replay fence — only counters STRICTLY greater than
      the last accepted one match, so a code (or an adjacent-window
      predecessor) can never be replayed once spent.

    Comparison is constant-time per candidate step (``hmac.compare_digest``).
    """
    if not code:
        return None
    normalized = code.strip()
    if len(normalized) != TOTP_DIGITS or not normalized.isdigit():
        return None
    drift = settings.MFA_TOTP_DRIFT_STEPS if drift_steps is None else drift_steps
    current = totp_counter(at)
    matched: int | None = None
    for step in range(current - drift, current + drift + 1):
        if step < 0:
            continue
        if min_counter is not None and step <= min_counter:
            continue
        # No early exit: check every candidate step so timing does not
        # reveal which step (if any) matched.
        if hmac.compare_digest(hotp(secret_b32, step), normalized):
            matched = step
    return matched


def build_otpauth_uri(secret_b32: str, account: str, issuer: str) -> str:
    """Provisioning URI (``otpauth://totp/...``) for authenticator apps."""
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret_b32}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


# At-rest encryption of TOTP seeds


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """The process-wide Fernet keyring for TOTP seeds.

    Uses the dedicated ``MFA_ENCRYPTION_KEY`` when configured (recommended
    in production so seed encryption is independent of the JWT signing
    key); otherwise derives a stable key from ``JWT_SECRET_KEY`` via
    SHA-256 with a domain-separation label — a disclosed trade-off
    (ADR-0014): key separation by derivation, not by independent secret.
    """
    configured = settings.MFA_ENCRYPTION_KEY
    if configured:
        return Fernet(configured)
    derived = hashlib.sha256(
        f"{settings.JWT_SECRET_KEY}::operator-mfa-totp".encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_totp_secret(secret_b32: str) -> str:
    """Encrypt a plaintext base32 seed for persistence."""
    return _fernet().encrypt(secret_b32.encode("utf-8")).decode("ascii")


def decrypt_totp_secret(ciphertext: str) -> str | None:
    """Decrypt a persisted seed; ``None`` (fail closed) on any failure.

    A key rotation or corrupted row must surface as "verification fails"
    (operator falls back to recovery codes / break-glass re-enrollment),
    never as an exception on the login path and never as a silent bypass.
    """
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, binascii.Error):
        return None


# Recovery codes


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Fresh single-use recovery codes (grouped hex, 80-bit entropy each)."""
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(_RECOVERY_CODE_BYTES)
        codes.append(f"{raw[:5]}-{raw[5:10]}-{raw[10:15]}-{raw[15:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    """SHA-256 digest of a normalized recovery code (never stored plaintext).

    Normalization (case, separators, whitespace) keeps hand-typed codes
    forgiving without weakening the 80-bit entropy the digest protects.
    """
    normalized = code.strip().lower().replace("-", "").replace(" ", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def looks_like_recovery_code(code: str) -> bool:
    """Cheap shape check to route a submitted proof to the right verifier."""
    normalized = code.strip().lower().replace("-", "").replace(" ", "")
    return len(normalized) == _RECOVERY_CODE_BYTES * 2
