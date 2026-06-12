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


def _login_and_capture_otp(client, email="frank@mail.com", password=VALID_PASSWORD):
    """Drive login and capture the plaintext OTP from the (mocked) email.

    The DB stores only the SHA-256 digest of the code so the
    plaintext can only be observed where a real user would see it: the
    outgoing email. _send_otp_email(recipient, otp_code) is patched and the
    code is read from its call args.
    """
    with patch("app.modules.auth.service.AuthService._send_otp_email") as send:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
    assert resp.status_code == 200
    return send.call_args[0][1]


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
        code = _login_and_capture_otp(client)

        # The DB never holds the plaintext code
        from app.modules.auth.models import OTPCode

        otp = (
            db.query(OTPCode)
            .filter(OTPCode.is_used.is_(False))
            .order_by(OTPCode.created_at.desc())
            .first()
        )
        assert otp is not None
        assert otp.code != code

        response = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": code},
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
        correct_code = _login_and_capture_otp(client)
        otp = (
            db.query(OTPCode)
            .filter(OTPCode.is_used.is_(False))
            .order_by(OTPCode.created_at.desc())
            .first()
        )
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
        code = _login_and_capture_otp(client)

        # First use — should succeed
        first = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": code},
        )
        assert first.status_code == 200

        # Second use — should fail with the generic 401
        response = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "frank@mail.com", "code": code},
        )
        assert response.status_code == 401


# Token Refresh Tests


def _login_and_get_refresh_token(client, db):
    """Drive login + verify-otp and return a valid refresh token."""
    code = _login_and_capture_otp(client)
    verify_resp = client.post(
        "/api/v1/auth/verify-otp",
        json={"email": "frank@mail.com", "code": code},
    )
    assert verify_resp.status_code == 200
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
        """Replaying a rotated-away token is rejected AND kills the family.

        Reuse of an already-spent refresh token is theft evidence
        one of the two parties holding the token is an
        attacker, and we cannot tell which. The replay itself 401s and the
        whole token family is fenced — including the freshly rotated
        descendant the thief would otherwise keep — forcing a clean
        re-authentication.
        """
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

        # Family revocation: the replay fenced the whole chain, so even the
        # freshly issued token is now rejected.
        fenced = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": new_refresh},
        )
        assert fenced.status_code == 401

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
