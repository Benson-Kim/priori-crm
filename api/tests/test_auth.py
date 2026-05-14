"""Tests for the authentication flow: login → OTP → token refresh."""

from unittest.mock import patch

from app.common.security import hash_password
from app.modules.auth.models import User


def _seed_user(db, email="frank@mail.com", password="securepass123"):
    """Helper to create a test user in the database."""
    user = User(
        email=email,
        password_hash=hash_password(password),
        first_name="Frank",
        last_name="Degods",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# Login Tests


class TestLogin:
    """POST /api/v1/auth/login"""

    def test_login_success(self, client, db):
        _seed_user(db)
        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            response = client.post(
                "/api/v1/auth/login",
                json={"email": "frank@mail.com", "password": "securepass123"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "Verification code sent" in data["message"]

    def test_login_wrong_password(self, client, db):
        _seed_user(db)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "frank@mail.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_email(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@mail.com", "password": "securepass123"},
        )
        assert response.status_code == 401

    def test_login_invalid_email_format(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "not-an-email", "password": "securepass123"},
        )
        assert response.status_code == 422


# OTP Verification Tests


class TestVerifyOTP:
    """POST /api/v1/auth/verify-otp"""

    def test_verify_otp_success(self, client, db):
        _seed_user(db)
        # Trigger login to create OTP
        with patch(
            "app.modules.auth.service.AuthService._send_otp_email"
        ) as mock_send:
            client.post(
                "/api/v1/auth/login",
                json={"email": "frank@mail.com", "password": "securepass123"},
            )

        # Extract OTP from database
        from app.modules.auth.models import OTPCode

        otp = (
            db.query(OTPCode)
            .filter(OTPCode.is_used.is_(False))
            .order_by(OTPCode.created_at.desc())
            .first()
        )
        assert otp is not None

        response = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": otp.code},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["email"] == "frank@mail.com"

    def test_verify_otp_invalid_code(self, client, db):
        _seed_user(db)
        response = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": "000000"},
        )
        assert response.status_code == 400

    def test_verify_otp_reuse_blocked(self, client, db):
        _seed_user(db)
        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            client.post(
                "/api/v1/auth/login",
                json={"email": "frank@mail.com", "password": "securepass123"},
            )

        from app.modules.auth.models import OTPCode

        otp = (
            db.query(OTPCode)
            .filter(OTPCode.is_used.is_(False))
            .order_by(OTPCode.created_at.desc())
            .first()
        )

        # First use — should succeed
        client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": otp.code},
        )

        # Second use — should fail
        response = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": otp.code},
        )
        assert response.status_code == 400


# Token Refresh Tests


class TestRefreshToken:
    """POST /api/v1/auth/refresh"""

    def test_refresh_success(self, client, db):
        _seed_user(db)

        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            client.post(
                "/api/v1/auth/login",
                json={"email": "frank@mail.com", "password": "securepass123"},
            )

        from app.modules.auth.models import OTPCode

        otp = (
            db.query(OTPCode)
            .filter(OTPCode.is_used.is_(False))
            .order_by(OTPCode.created_at.desc())
            .first()
        )

        verify_resp = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": otp.code},
        )
        refresh_token = verify_resp.json()["refresh_token"]

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    def test_refresh_invalid_token(self, client):
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )
        assert response.status_code == 401
