from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.common.validators import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    validate_password_strength,
)


# Request Schemas
class LoginRequest(BaseModel):
    """Login request with email and password."""

    email: EmailStr
    password: str = Field(
        ..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, v: str) -> str:
        """Enforce the shared password policy."""
        return validate_password_strength(v)


class VerifyOTPRequest(BaseModel):
    """OTP verification request."""

    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class RefreshTokenRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Logout request: the refresh token to revoke."""

    refresh_token: str

class RegisterRequest(BaseModel):
    """Registration request for creating the first admin user."""

    email: EmailStr
    password: str = Field(
        ..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    role: str = Field(default="admin")

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, v: str) -> str:
        """Enforce the shared password policy."""
        return validate_password_strength(v)



# Response Schemas
class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


class UserResponse(BaseModel):
    """Public user data returned in API responses."""

    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    avatar_url: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: str | UUID) -> str:
        """Accept a uuid.UUID (as returned by Postgres) and serialize as str."""
        return str(v) if isinstance(v, UUID) else v


class TokenResponse(BaseModel):
    """JWT token pair returned after successful OTP verification."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshResponse(BaseModel):
    """Response for token refresh.

    Returns a rotated token pair: the presented refresh token is
    revoked server-side, so the new refresh token must be persisted by the
    client and used for the next refresh.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
