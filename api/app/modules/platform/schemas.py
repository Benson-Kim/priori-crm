"""Platform-operator MFA schemas (ADR-0014, issue #73)."""

from pydantic import BaseModel, Field


class MfaStatusResponse(BaseModel):
    """Enrollment state of the authenticated operator."""

    enrolled: bool
    pending: bool
    recovery_codes_remaining: int


class MfaEnrollmentResponse(BaseModel):
    """One-time provisioning material for a (re)started enrollment.

    The plaintext secret and otpauth URI exist ONLY in this response —
    the server persists the Fernet ciphertext and never logs either.
    """

    secret: str
    otpauth_uri: str
    message: str


class MfaActivationRequest(BaseModel):
    """A live authenticator code confirming a pending enrollment."""

    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class MfaActivationResponse(BaseModel):
    """Recovery codes issued exactly once at activation."""

    recovery_codes: list[str]
    message: str
