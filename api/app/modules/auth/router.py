from fastapi import APIRouter, Depends

from app.common.dependencies import DbSession, verify_internal_secret
from app.modules.auth.schemas import (
    LoginRequest,
    MessageResponse,
    RefreshResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserResponse,
    VerifyOTPRequest,
)
from app.modules.auth.service import AuthService

router = APIRouter()


@router.post("/login", response_model=MessageResponse)
def login(body: LoginRequest, db: DbSession):
    """Step 1: Validate credentials and send OTP to user's email."""
    auth_service = AuthService(db)
    masked_email = auth_service.login(body.email, body.password)
    return MessageResponse(message=f"Verification code sent to {masked_email}")


@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp(body: VerifyOTPRequest, db: DbSession):
    """Step 2: Verify OTP and return JWT tokens."""
    auth_service = AuthService(db)
    access_token, refresh_token, user = auth_service.verify_otp(body.email, body.code)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh_token(body: RefreshTokenRequest, db: DbSession):
    """Issue a new access token using a valid refresh token."""
    auth_service = AuthService(db)
    new_access_token = auth_service.refresh_access_token(body.refresh_token)
    return RefreshResponse(access_token=new_access_token)


@router.post(
    "/internal/purge-otps",
    response_model=MessageResponse,
    include_in_schema=False,
    dependencies=[Depends(verify_internal_secret)],
)
def purge_otps(db: DbSession):
    """Internal: delete used/expired OTP rows (AUTH-DBA-2).

    Protected by the internal machine-to-machine secret; intended to be
    called by a scheduler. Mirrors the expenses overdue-transition trigger.
    """
    auth_service = AuthService(db)
    deleted = auth_service.purge_expired_otps()
    return MessageResponse(message=f"Purged {deleted} expired/used OTP rows.")
