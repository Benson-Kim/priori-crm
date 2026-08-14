from fastapi import APIRouter, Depends

from app.common.dependencies import DbSession, verify_internal_secret
from app.common.routing import CommitOnSuccessRoute
from app.modules.auth.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshResponse,
    RefreshTokenRequest,
    ResendOTPRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
    VerifyOTPRequest,
)
from app.modules.auth.service import AuthService

router = APIRouter(route_class=CommitOnSuccessRoute)


@router.post("/login", response_model=MessageResponse)
def login(body: LoginRequest, db: DbSession):
    """Step 1: Validate credentials and send OTP to user's email.

    Returns a generic confirmation that never reveals whether the account
    exists, so the endpoint cannot be used to enumerate users.
    """
    auth_service = AuthService(db)
    auth_service.login(body.email, body.password)
    return MessageResponse(
        message="If the credentials are valid, a verification code has been sent."
    )


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


@router.post("/resend-otp", response_model=MessageResponse)
def resend_otp(body: ResendOTPRequest, db: DbSession):
    """Resend OTP to user's email."""
    auth_service = AuthService(db)
    auth_service.resend_otp(body.email)
    return MessageResponse(
        message="If the account exists, a new verification code has been sent."
    )


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(body: ForgotPasswordRequest, db: DbSession):
    """Request a password-reset link.

    Always returns the same generic confirmation regardless of whether the
    account exists, so the endpoint cannot be used to enumerate users.
    """
    auth_service = AuthService(db)
    auth_service.request_password_reset(body.email)
    return MessageResponse(
        message=(
            "If an account exists for that email, a password reset link has been sent."
        )
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(body: ResetPasswordRequest, db: DbSession):
    """Complete a password reset with the emailed token and a new password.

    An invalid / expired / used token surfaces a single generic 401. On
    success the password is changed and all existing sessions are revoked;
    the user signs in again normally (this endpoint does not issue tokens).
    """
    auth_service = AuthService(db)
    auth_service.reset_password(body.token, body.new_password)
    return MessageResponse(message="Your password has been reset. You can now sign in.")


@router.post("/refresh", response_model=RefreshResponse)
def refresh_token(body: RefreshTokenRequest, db: DbSession):
    """Rotate tokens: issue a new access + refresh pair.

    The presented refresh token is revoked, so the client must store and use
    the returned refresh token for its next refresh.
    """
    auth_service = AuthService(db)
    access_token, refresh_token = auth_service.refresh_access_token(body.refresh_token)
    return RefreshResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(body: LogoutRequest, db: DbSession):
    """Revoke the presented refresh token.

    Idempotent: a missing/invalid/expired token still returns the same generic
    confirmation, so logout never leaks token state and never errors.
    """
    auth_service = AuthService(db)
    auth_service.logout(body.refresh_token)
    return MessageResponse(message="Logged out.")


@router.post(
    "/internal/purge-otps",
    response_model=MessageResponse,
    include_in_schema=False,
    dependencies=[Depends(verify_internal_secret)],
)
def purge_otps(db: DbSession):
    """Internal: delete used/expired OTP rows .

    Protected by the internal machine-to-machine secret; intended to be
    called by a scheduler.
    """
    auth_service = AuthService(db)
    deleted = auth_service.purge_expired_otps()
    return MessageResponse(message=f"Purged {deleted} expired/used OTP rows.")
