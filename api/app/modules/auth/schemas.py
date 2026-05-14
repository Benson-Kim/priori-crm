from pydantic import BaseModel, EmailStr, Field


# Request Schemas
class LoginRequest(BaseModel):
    """Login request with email and password."""

    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class VerifyOTPRequest(BaseModel):
    """OTP verification request."""

    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class RefreshTokenRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


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
    avatar_url: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """JWT token pair returned after successful OTP verification."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshResponse(BaseModel):
    """Response for token refresh — new access token only."""

    access_token: str
    token_type: str = "bearer"
