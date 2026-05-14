import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.common.exceptions import BadRequestException, NotFoundException, UnauthorizedException
from app.common.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.lib.config import settings
from app.lib.email import email_service
from app.modules.auth.models import OTPCode, User

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 5


class AuthService:
    """Handles authentication business logic following Single Responsibility Principle."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # Login (Step 1)

    def login(self, email: str, password: str) -> str:
        """Validate credentials and send an OTP code. Returns masked email."""
        user = self._get_user_by_email(email)
        if user is None:
            raise UnauthorizedException("Invalid email or password.")

        if not verify_password(password, user.password_hash):
            raise UnauthorizedException("Invalid email or password.")

        if not user.is_active:
            raise UnauthorizedException("Account is deactivated.")

        otp_code = self._create_otp(user)
        self._send_otp_email(user.email, otp_code)

        return self._mask_email(user.email)

    # Verify OTP (Step 2)

    def verify_otp(self, email: str, code: str) -> tuple[str, str, User]:
        """Verify OTP and return (access_token, refresh_token, user)."""
        user = self._get_user_by_email(email)
        if user is None:
            raise UnauthorizedException("Invalid email or OTP.")

        otp = (
            self._db.query(OTPCode)
            .filter(
                OTPCode.user_id == user.id,
                OTPCode.code == code,
                OTPCode.is_used.is_(False),
            )
            .order_by(OTPCode.created_at.desc())
            .first()
        )

        if otp is None:
            raise BadRequestException("Invalid OTP code.")

        if otp.is_expired:
            raise BadRequestException("OTP code has expired. Please request a new one.")

        # Mark OTP as used
        otp.is_used = True
        self._db.commit()

        # Invalidate all other unused OTPs for this user
        self._invalidate_pending_otps(user.id, exclude_id=otp.id)

        access_token = create_access_token(subject=str(user.id))
        refresh_token = create_refresh_token(subject=str(user.id))

        return access_token, refresh_token, user

    # Refresh Token

    def refresh_access_token(self, refresh_token: str) -> str:
        """Validate refresh token and issue a new access token."""
        payload = decode_refresh_token(refresh_token)
        user_id = payload.get("sub")

        user = self._db.query(User).filter(User.id == user_id).first()
        if user is None or not user.is_active:
            raise UnauthorizedException("Invalid or inactive user.")

        return create_access_token(subject=str(user.id))

    # User Seeding (for development)

    def seed_user(
        self,
        email: str,
        password: str,
        first_name: str = "Frank",
        last_name: str = "Degods",
    ) -> User:
        """Create a user if one doesn't exist — for development seeding only."""
        existing = self._get_user_by_email(email)
        if existing:
            return existing

        user = User(
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
        )
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        logger.info("Seeded user: %s", email)
        return user

    # Private Helpers

    def _get_user_by_email(self, email: str) -> User | None:
        """Look up a user by email address."""
        return self._db.query(User).filter(User.email == email).first()

    def _create_otp(self, user: User) -> str:
        """Generate a 6-digit OTP, persist it, and return the code string."""
        # Invalidate any pending OTPs first
        self._invalidate_pending_otps(user.id)

        code = "".join(secrets.choice("0123456789") for _ in range(6))
        otp = OTPCode(
            user_id=user.id,
            code=code,
            expires_at=datetime.now(UTC) + timedelta(minutes=OTP_EXPIRY_MINUTES),
        )
        self._db.add(otp)
        self._db.commit()
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
        self._db.commit()

    def _send_otp_email(self, recipient: str, otp_code: str) -> None:
        """Send OTP via email. Log-only in development if SES is not configured."""
        if settings.ENVIRONMENT == "development" and not settings.AWS_ACCESS_KEY_ID:
            logger.warning(
                "DEV MODE — OTP for %s: %s (email not sent, SES not configured)",
                recipient,
                otp_code,
            )
            return

        sent = email_service.send_otp(recipient, otp_code)
        if not sent:
            raise BadRequestException("Failed to send verification email. Try again.")

    @staticmethod
    def _mask_email(email: str) -> str:
        """Mask an email address (e.g. fra**********18@mail.com)."""
        local, domain = email.split("@")
        if len(local) <= 5:
            return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"
        start = local[:3]
        end = local[-2:]
        masked = "*" * min(len(local) - 5, 10)
        return f"{start}{masked}{end}@{domain}"
