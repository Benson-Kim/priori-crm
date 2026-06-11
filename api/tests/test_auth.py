"""Tests for the authentication flow: login → OTP → token refresh."""

from unittest.mock import patch

from app.common.security import hash_password
from app.modules.auth.models import User

# A password that satisfies the shared password policy: >= 8 chars
# with an uppercase letter, a lowercase letter, a digit and a special char.
VALID_PASSWORD = "Securepass123!"


def _seed_user(db, email="frank@mail.com", password=VALID_PASSWORD):
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
                json={"email": "frank@mail.com", "password": VALID_PASSWORD},
            )
        assert response.status_code == 200
        data = response.json()
        # Generic, non-enumerating confirmation.
        assert "verification code" in data["message"].lower()

    def test_login_wrong_password(self, client, db):
        _seed_user(db)
        # Policy-valid but incorrect, so it reaches the credential check and
        # returns the generic 401 rather than a 422 policy rejection.
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "frank@mail.com", "password": "Wrongpass123!"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_email(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@mail.com", "password": VALID_PASSWORD},
        )
        assert response.status_code == 401

    def test_login_invalid_email_format(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "not-an-email", "password": VALID_PASSWORD},
        )
        assert response.status_code == 422


# OTP Verification Tests


class TestVerifyOTP:
    """POST /api/v1/auth/verify-otp"""

    def test_verify_otp_success(self, client, db):
        _seed_user(db)
        # Trigger login to create OTP
        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            client.post(
                "/api/v1/auth/login",
                json={"email": "frank@mail.com", "password": VALID_PASSWORD},
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
        # Generic 401 (anti-enumeration): a wrong code is indistinguishable
        # from a non-existent account or an expired code.
        assert response.status_code == 401

    def test_verify_otp_attempts_capped(self, client, db):
        """After AUTH_MAX_OTP_ATTEMPTS wrong guesses the code is consumed."""
        from app.lib.config import settings
        from app.modules.auth.models import OTPCode

        _seed_user(db)
        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            client.post(
                "/api/v1/auth/login",
                json={"email": "frank@mail.com", "password": VALID_PASSWORD},
            )
        otp = (
            db.query(OTPCode)
            .filter(OTPCode.is_used.is_(False))
            .order_by(OTPCode.created_at.desc())
            .first()
        )
        correct_code = otp.code
        wrong = "999999" if correct_code != "999999" else "111111"

        # Exhaust the attempt budget with wrong codes.
        for _ in range(settings.AUTH_MAX_OTP_ATTEMPTS):
            resp = client.post(
                "/api/v1/auth/verify-otp",
                json={"email": "frank@mail.com", "code": wrong},
            )
            assert resp.status_code == 401

        # The (now consumed) code must no longer be accepted.
        db.expire_all()
        consumed = db.query(OTPCode).filter(OTPCode.id == otp.id).first()
        assert consumed.is_used is True
        resp = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": correct_code},
        )
        assert resp.status_code == 401

    def test_verify_otp_reuse_blocked(self, client, db):
        _seed_user(db)
        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            client.post(
                "/api/v1/auth/login",
                json={"email": "frank@mail.com", "password": VALID_PASSWORD},
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

        # Second use — should fail with the generic 401
        response = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": otp.code},
        )
        assert response.status_code == 401


# Token Refresh Tests


def _login_and_get_refresh_token(client, db):
    """Drive login + verify-otp and return a valid refresh token."""
    from app.modules.auth.models import OTPCode

    with patch("app.modules.auth.service.AuthService._send_otp_email"):
        client.post(
            "/api/v1/auth/login",
            json={"email": "frank@mail.com", "password": VALID_PASSWORD},
        )
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
    return verify_resp.json()["refresh_token"]


class TestRefreshToken:
    """POST /api/v1/auth/refresh"""

    def test_refresh_success_rotates_pair(self, client, db):
        _seed_user(db)
        refresh_token = _login_and_get_refresh_token(client, db)

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        data = response.json()
        # Rotation: a new access AND a new refresh token come back,
        # and the refresh token is genuinely rotated (not the same string).
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["refresh_token"] != refresh_token

    def test_old_refresh_token_revoked_after_rotation(self, client, db):
        """The presented token is denylisted, so reusing it is rejected."""
        _seed_user(db)
        refresh_token = _login_and_get_refresh_token(client, db)

        first = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert first.status_code == 200
        new_refresh = first.json()["refresh_token"]

        # Reusing the rotated-away token must fail.
        replay = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert replay.status_code == 401

        # The freshly issued token still works.
        ok = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": new_refresh},
        )
        assert ok.status_code == 200

    def test_refresh_invalid_token(self, client):
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )
        assert response.status_code == 401


# Logout Tests


class TestLogout:
    """POST /api/v1/auth/logout"""

    def test_logout_revokes_refresh_token(self, client, db):
        _seed_user(db)
        refresh_token = _login_and_get_refresh_token(client, db)

        logout_resp = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
        )
        assert logout_resp.status_code == 200

        # The revoked token can no longer be refreshed.
        refresh_resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_resp.status_code == 401

    def test_logout_is_idempotent_for_invalid_token(self, client):
        """A malformed/unknown token is a safe no-op, not an error."""
        response = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "not.a.valid.token"},
        )
        assert response.status_code == 200
