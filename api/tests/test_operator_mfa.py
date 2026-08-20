"""Operator MFA + step-up + /platform IP allowlist (ADR-0014, issue #73).

Pins the contract:

- RFC 6238 correctness (Appendix B test vectors) for the stdlib TOTP;
- enrollment lifecycle: an UNENROLLED operator signs in but receives a
  constrained enrollment-only token (403 everywhere under /platform
  except /platform/mfa); activation returns single-use recovery codes
  exactly once and flips enforcement on;
- enrolled sign-in REQUIRES the second factor, failing with the SAME
  generic 401 body as any credential failure (enumeration safety), and
  the emailed OTP is NOT burnt by a wrong second factor;
- drift window (±1 step default), replay rejection (login and step-up
  share one monotonic fence), attempt rate limiting (429);
- step-up on destructive routes: X-MFA-Code demanded per action, expired
  /replayed codes rejected, grants AND denials audited (denial committed
  despite the 401), recovery codes usable and single-use;
- secret rotation demands step-up; QA finding 09 (no account creation);
- IP allowlist: disabled when empty, CIDR-enforced when set, fail-closed
  on unparseable client addresses and malformed configuration.
"""

import base64
from unittest.mock import patch

import pytest

from app.common.audit import AuditEvent
from app.common.mfa import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_recovery_code,
    hotp,
    looks_like_recovery_code,
    totp_at,
    totp_counter,
    verify_totp,
)
from app.common.security import create_access_token, hash_password
from app.constants.enums import UserRole
from app.lib.config import settings
from app.modules.auth.models import User
from app.modules.owner.service import OwnerService
from app.modules.platform.models import OperatorMfaTotp, OperatorRecoveryCode
from tests.operator_mfa_utils import auth_headers, ensure_enrolled

PASSWORD = "Sup3r!Secret1"
MFA_URL = "/api/v1/platform/mfa"
OWNERS_URL = "/api/v1/platform/owners"


def _seed_user(db, email: str, role: UserRole) -> User:
    user = User(
        email=email,
        password_hash=hash_password(PASSWORD),
        first_name="Test",
        last_name="User",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_operator(db) -> User:
    return _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)


def _seed_owner(db):
    profile = OwnerService(db).get_or_create()
    db.commit()
    return profile.id


def _login_and_capture_otp(client, email: str) -> str:
    with patch("app.modules.auth.service.AuthService._send_otp_email") as send:
        resp = client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert resp.status_code == 200
        return send.call_args[0][1]


def _verify_otp(client, email: str, otp: str, **second_factor):
    return client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": otp, **second_factor},
    )


def _sign_in(client, email: str, **second_factor):
    """Full login → verify-otp; returns the verify-otp response."""
    otp = _login_and_capture_otp(client, email)
    return _verify_otp(client, email, otp, **second_factor)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mfa_events(db, user_id) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "operator_mfa",
            AuditEvent.entity_id == user_id,
        )
        .order_by(AuditEvent.created_at)
        .all()
    )


@pytest.mark.no_db
class TestRfc6238Vectors:
    """The stdlib TOTP must match RFC 6238 Appendix B (SHA-1 rows)."""

    SECRET_B32 = base64.b32encode(b"12345678901234567890").decode()

    @pytest.mark.parametrize(
        ("timestamp", "expected"),
        [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ],
    )
    def test_appendix_b_sha1_vectors(self, timestamp, expected):
        counter = totp_counter(timestamp)
        assert hotp(self.SECRET_B32, counter, digits=8) == expected

    def test_six_digit_code_is_suffix_of_dynamic_truncation(self):
        assert hotp(self.SECRET_B32, totp_counter(59)) == "94287082"[-6:]


@pytest.mark.no_db
class TestTotpVerification:
    SECRET_B32 = base64.b32encode(b"12345678901234567890").decode()

    def test_drift_window_accepts_adjacent_steps_only(self):
        now = 1_700_000_000
        current = totp_counter(now)
        for offset in (-1, 0, 1):
            code = hotp(self.SECRET_B32, current + offset)
            assert (
                verify_totp(self.SECRET_B32, code, at=now, drift_steps=1)
                == current + offset
            ), offset
        for offset in (-2, 2):
            code = hotp(self.SECRET_B32, current + offset)
            assert verify_totp(self.SECRET_B32, code, at=now, drift_steps=1) is None

    def test_replay_fence_rejects_spent_and_older_counters(self):
        now = 1_700_000_000
        current = totp_counter(now)
        code = hotp(self.SECRET_B32, current)
        matched = verify_totp(self.SECRET_B32, code, at=now, drift_steps=1)
        assert matched == current
        # Same code again, and the previous step's code, both die on the fence.
        assert (
            verify_totp(
                self.SECRET_B32, code, at=now, drift_steps=1, min_counter=matched
            )
            is None
        )
        older = hotp(self.SECRET_B32, current - 1)
        assert (
            verify_totp(
                self.SECRET_B32, older, at=now, drift_steps=1, min_counter=matched
            )
            is None
        )

    def test_malformed_codes_rejected(self):
        for bad in (None, "", "12345", "1234567", "abcdef", "12 456"):
            assert verify_totp(self.SECRET_B32, bad, at=59) is None


@pytest.mark.no_db
class TestSecretAndRecoveryPrimitives:
    def test_encrypt_decrypt_roundtrip_and_fail_closed(self):
        secret = "JBSWY3DPEHPK3PXP"
        ciphertext = encrypt_totp_secret(secret)
        assert secret not in ciphertext
        assert decrypt_totp_secret(ciphertext) == secret
        assert decrypt_totp_secret("not-a-token") is None

    def test_recovery_codes_shape_and_hash_normalization(self):
        codes = generate_recovery_codes()
        assert len(codes) == 10
        assert len(set(codes)) == 10
        for code in codes:
            assert looks_like_recovery_code(code)
            assert hash_recovery_code(code) == hash_recovery_code(
                code.upper().replace("-", " ")
            )
        assert not looks_like_recovery_code("123456")


class TestUnenrolledOperatorConstrainedPath:
    def test_sign_in_yields_enrollment_only_token(self, client, db):
        operator = _seed_operator(db)
        _seed_owner(db)
        resp = _sign_in(client, operator.email)
        assert resp.status_code == 200
        token = resp.json()["access_token"]

        # Constrained: the console proper is 403 ...
        assert client.get(OWNERS_URL, headers=_bearer(token)).status_code == 403
        assert (
            client.get("/api/v1/platform/audit", headers=_bearer(token)).status_code
            == 403
        )
        # ... but the enrollment surface is reachable.
        status = client.get(MFA_URL, headers=_bearer(token))
        assert status.status_code == 200
        assert status.json() == {
            "enrolled": False,
            "pending": False,
            "recovery_codes_remaining": 0,
        }

    def test_claimless_operator_token_is_constrained_fail_closed(self, client, db):
        operator = _seed_operator(db)
        token = create_access_token(subject=str(operator.id))  # no mfa claim
        assert client.get(OWNERS_URL, headers=_bearer(token)).status_code == 403
        assert client.get(MFA_URL, headers=_bearer(token)).status_code == 200

    def test_enrollment_flow_then_full_access_after_totp_sign_in(self, client, db):
        operator = _seed_operator(db)
        _seed_owner(db)
        enroll_token = _sign_in(client, operator.email).json()["access_token"]

        started = client.post(f"{MFA_URL}/enrollment", headers=_bearer(enroll_token))
        assert started.status_code == 200
        body = started.json()
        secret = body["secret"]
        assert body["otpauth_uri"].startswith("otpauth://totp/")
        assert secret in body["otpauth_uri"]

        activated = client.post(
            f"{MFA_URL}/enrollment/activate",
            json={"code": totp_at(secret)},
            headers=_bearer(enroll_token),
        )
        assert activated.status_code == 200
        codes = activated.json()["recovery_codes"]
        assert len(codes) == 10
        # Only digests are persisted.
        stored = db.query(OperatorRecoveryCode).all()
        assert len(stored) == 10
        assert {r.code_hash for r in stored} == {hash_recovery_code(c) for c in codes}
        assert not set(codes) & {r.code_hash for r in stored}

        # The pre-enrollment token stays constrained: re-auth is required.
        assert client.get(OWNERS_URL, headers=_bearer(enroll_token)).status_code == 403

        # A fresh TOTP sign-in yields full console access. The activation
        # consumed the current step's counter (replay fence), so present
        # the NEXT step's code — inside the ±1 drift window.
        next_code = hotp(secret, totp_counter() + 1)
        resp = _sign_in(client, operator.email, totp_code=next_code)
        assert resp.status_code == 200
        full_token = resp.json()["access_token"]
        assert client.get(OWNERS_URL, headers=_bearer(full_token)).status_code == 200

        actions = [e.action for e in _mfa_events(db, operator.id)]
        assert actions == ["mfa_enrollment_started", "mfa_enrolled"]

    def test_activation_without_pending_enrollment_is_400(self, client, db):
        operator = _seed_operator(db)
        token = _sign_in(client, operator.email).json()["access_token"]
        resp = client.post(
            f"{MFA_URL}/enrollment/activate",
            json={"code": "123456"},
            headers=_bearer(token),
        )
        assert resp.status_code == 400

    def test_wrong_activation_code_401_and_audited(self, client, db):
        operator = _seed_operator(db)
        token = _sign_in(client, operator.email).json()["access_token"]
        client.post(f"{MFA_URL}/enrollment", headers=_bearer(token))
        resp = client.post(
            f"{MFA_URL}/enrollment/activate",
            json={"code": "000000"},
            headers=_bearer(token),
        )
        assert resp.status_code == 401
        actions = [e.action for e in _mfa_events(db, operator.id)]
        assert actions == ["mfa_enrollment_started", "mfa_activation_failed"]

    def test_mfa_endpoints_never_create_or_promote_accounts(self, client, db):
        """QA finding 09: enrollment acts on the caller only."""
        operator = _seed_operator(db)
        token = _sign_in(client, operator.email).json()["access_token"]
        users_before = db.query(User).count()
        client.post(f"{MFA_URL}/enrollment", headers=_bearer(token))
        client.get(MFA_URL, headers=_bearer(token))
        db.expire_all()
        assert db.query(User).count() == users_before
        assert db.get(User, operator.id).role == "platform_operator"


class TestEnrolledSignIn:
    def test_totp_required_and_generic_401_enumeration_safe(self, client, db):
        operator = _seed_operator(db)
        secret = ensure_enrolled(db, operator)

        # Missing second factor: generic 401 ...
        otp = _login_and_capture_otp(client, operator.email)
        missing = _verify_otp(client, operator.email, otp)
        assert missing.status_code == 401

        # ... byte-identical in error body to a wrong OTP for a TENANT user
        # (the canonical generic credential failure).
        _seed_user(db, "member@mail.com", UserRole.MEMBER)
        _login_and_capture_otp(client, "member@mail.com")
        wrong_otp = _verify_otp(client, "member@mail.com", "000000")
        assert wrong_otp.status_code == 401
        assert missing.json()["error"] == wrong_otp.json()["error"]

        # Wrong TOTP: same generic 401 again.
        wrong_totp = _verify_otp(client, operator.email, otp, totp_code="000000")
        assert wrong_totp.status_code == 401
        assert wrong_totp.json()["error"] == wrong_otp.json()["error"]

        # The emailed OTP was NOT burnt by the failed second factor: the
        # same OTP with the right TOTP now succeeds.
        good = _verify_otp(client, operator.email, otp, totp_code=totp_at(secret))
        assert good.status_code == 200
        assert good.json()["user"]["role"] == "platform_operator"

    def test_totp_sign_in_replay_rejected(self, client, db):
        operator = _seed_operator(db)
        secret = ensure_enrolled(db, operator)
        code = totp_at(secret)
        assert _sign_in(client, operator.email, totp_code=code).status_code == 200
        # Same code again: dead on the replay fence.
        assert _sign_in(client, operator.email, totp_code=code).status_code == 401

    def test_recovery_code_sign_in_is_single_use(self, client, db):
        operator = _seed_operator(db)
        ensure_enrolled(db, operator)
        codes = generate_recovery_codes(2)
        for code in codes:
            db.add(
                OperatorRecoveryCode(
                    user_id=operator.id, code_hash=hash_recovery_code(code)
                )
            )
        db.commit()

        first = _sign_in(client, operator.email, recovery_code=codes[0])
        assert first.status_code == 200
        again = _sign_in(client, operator.email, recovery_code=codes[0])
        assert again.status_code == 401
        assert any(
            e.action == "mfa_recovery_code_used" for e in _mfa_events(db, operator.id)
        )

    def test_second_factor_attempts_rate_limited(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "MFA_MAX_ATTEMPTS", 3)
        operator = _seed_operator(db)
        ensure_enrolled(db, operator)
        otp = _login_and_capture_otp(client, operator.email)
        for _ in range(3):
            resp = _verify_otp(client, operator.email, otp, totp_code="000000")
            assert resp.status_code == 401
        throttled = _verify_otp(client, operator.email, otp, totp_code="000000")
        assert throttled.status_code == 429

    def test_tenant_user_sign_in_ignores_second_factor_fields(self, client, db):
        _seed_user(db, "member@mail.com", UserRole.MEMBER)
        resp = _sign_in(client, "member@mail.com", totp_code="000000")
        assert resp.status_code == 200

    def test_refresh_preserves_constrained_scope(self, client, db):
        """An 'enroll' refresh token can never mint a full console token."""
        operator = _seed_operator(db)
        signin = _sign_in(client, operator.email)  # unenrolled → enroll scope
        refresh_token = signin.json()["refresh_token"]
        # Enroll AFTER the tokens were minted.
        ensure_enrolled(db, operator)
        refreshed = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refreshed.status_code == 200
        new_access = refreshed.json()["access_token"]
        assert client.get(OWNERS_URL, headers=_bearer(new_access)).status_code == 403
        assert client.get(MFA_URL, headers=_bearer(new_access)).status_code == 200


class TestStepUp:
    def _owner_and_operator(self, client, db):
        operator = _seed_operator(db)
        secret = ensure_enrolled(db, operator)
        owner_id = _seed_owner(db)
        token = create_access_token(subject=str(operator.id), extra={"mfa": "totp"})
        return operator, secret, owner_id, token

    def _status_url(self, owner_id) -> str:
        return f"{OWNERS_URL}/{owner_id}/status"

    def test_suspend_without_code_401_and_denial_audited(self, client, db):
        operator, _secret, owner_id, token = self._owner_and_operator(client, db)
        resp = client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers=_bearer(token),
        )
        assert resp.status_code == 401
        # The denial survives the 401 (explicit commit): evidence first.
        events = _mfa_events(db, operator.id)
        assert [e.action for e in events] == ["step_up_denied"]
        assert events[0].after == {"action": "owner_status_change"}
        # And the owner was NOT suspended.
        from app.modules.owner.models import OwnerProfile

        db.expire_all()
        assert db.get(OwnerProfile, owner_id).status == "active"

    def test_suspend_with_fresh_code_succeeds_and_grant_audited(self, client, db):
        operator, secret, owner_id, token = self._owner_and_operator(client, db)
        resp = client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": totp_at(secret)},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"
        events = _mfa_events(db, operator.id)
        assert [e.action for e in events] == ["step_up_granted"]
        assert events[0].after == {"action": "owner_status_change", "method": "totp"}

    def test_step_up_code_cannot_be_replayed(self, client, db):
        _operator, secret, owner_id, token = self._owner_and_operator(client, db)
        code = totp_at(secret)
        first = client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": code},
        )
        assert first.status_code == 200
        replay = client.patch(
            self._status_url(owner_id),
            json={"status": "active"},
            headers={**_bearer(token), "X-MFA-Code": code},
        )
        assert replay.status_code == 401
        db.expire_all()
        from app.modules.owner.models import OwnerProfile

        assert db.get(OwnerProfile, owner_id).status == "suspended"

    def test_expired_code_rejected(self, client, db):
        """Step-up 'expiry': a code older than the drift window is dead."""
        _operator, secret, owner_id, token = self._owner_and_operator(client, db)
        stale = hotp(secret, totp_counter() - 3)
        resp = client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": stale},
        )
        assert resp.status_code == 401

    def test_entitlement_change_requires_step_up(self, client, db):
        _operator, secret, owner_id, token = self._owner_and_operator(client, db)
        url = f"{OWNERS_URL}/{owner_id}/modules/customers"
        denied = client.patch(url, json={"enabled": False}, headers=_bearer(token))
        assert denied.status_code == 401
        granted = client.patch(
            url,
            json={"enabled": False},
            headers={**_bearer(token), "X-MFA-Code": totp_at(secret)},
        )
        assert granted.status_code == 200

    def test_recovery_code_works_for_step_up_once(self, client, db):
        operator, _secret, owner_id, token = self._owner_and_operator(client, db)
        code = generate_recovery_codes(1)[0]
        db.add(
            OperatorRecoveryCode(
                user_id=operator.id, code_hash=hash_recovery_code(code)
            )
        )
        db.commit()
        first = client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": code},
        )
        assert first.status_code == 200
        again = client.patch(
            self._status_url(owner_id),
            json={"status": "active"},
            headers={**_bearer(token), "X-MFA-Code": code},
        )
        assert again.status_code == 401

    def test_step_up_attempts_rate_limited(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "MFA_MAX_ATTEMPTS", 3)
        _operator, _secret, owner_id, token = self._owner_and_operator(client, db)
        for _ in range(3):
            resp = client.patch(
                self._status_url(owner_id),
                json={"status": "suspended"},
                headers={**_bearer(token), "X-MFA-Code": "000000"},
            )
            assert resp.status_code == 401
        throttled = client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": "000000"},
        )
        assert throttled.status_code == 429

    def test_step_up_fails_closed_without_active_enrollment(self, client, db):
        """A stale full token after an MFA reset cannot act destructively."""
        operator = _seed_operator(db)
        owner_id = _seed_owner(db)
        token = create_access_token(subject=str(operator.id), extra={"mfa": "totp"})
        resp = client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": "123456"},
        )
        assert resp.status_code == 401

    def test_step_up_events_visible_in_platform_audit(self, client, db):
        _operator, secret, owner_id, token = self._owner_and_operator(client, db)
        client.patch(
            self._status_url(owner_id),
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": totp_at(secret)},
        )
        resp = client.get(
            "/api/v1/platform/audit",
            headers={**_bearer(token)},
        )
        assert resp.status_code == 200
        actions = [i["action"] for i in resp.json()["items"]]
        assert "step_up_granted" in actions
        assert "owner_suspended" in actions


class TestRotation:
    def test_rotation_requires_step_up_and_reissues(self, client, db):
        operator = _seed_operator(db)
        secret = ensure_enrolled(db, operator)
        token = create_access_token(subject=str(operator.id), extra={"mfa": "totp"})

        denied = client.post(f"{MFA_URL}/enrollment", headers=_bearer(token))
        assert denied.status_code == 401

        rotated = client.post(
            f"{MFA_URL}/enrollment",
            headers={**_bearer(token), "X-MFA-Code": totp_at(secret)},
        )
        assert rotated.status_code == 200
        new_secret = rotated.json()["secret"]
        assert new_secret != secret

        # Until the new seed is activated, enrollment is pending again and
        # step-up fails closed (no ACTIVE enrollment).
        owner_id = _seed_owner(db)
        resp = client.patch(
            f"{OWNERS_URL}/{owner_id}/status",
            json={"status": "suspended"},
            headers={**_bearer(token), "X-MFA-Code": totp_at(new_secret)},
        )
        assert resp.status_code == 401

        activated = client.post(
            f"{MFA_URL}/enrollment/activate",
            json={"code": totp_at(new_secret)},
            headers=_bearer(token),
        )
        assert activated.status_code == 200
        row = (
            db.query(OperatorMfaTotp)
            .filter(OperatorMfaTotp.user_id == operator.id)
            .first()
        )
        db.refresh(row)
        assert row.status == "active"
        assert decrypt_totp_secret(row.secret_encrypted) == new_secret


class TestIpAllowlist:
    def test_empty_allowlist_is_disabled(self, client, db):
        operator = _seed_operator(db)
        assert settings.PLATFORM_IP_ALLOWLIST == ""
        resp = client.get(OWNERS_URL, headers=auth_headers(operator))
        assert resp.status_code == 200

    def test_unparseable_client_address_fails_closed(self, client, db, monkeypatch):
        # The TestClient socket host is the literal string "testclient" —
        # not an IP address — so with an allowlist active the check must
        # DENY, never skip.
        monkeypatch.setattr(settings, "PLATFORM_IP_ALLOWLIST", "10.0.0.0/8")
        operator = _seed_operator(db)
        resp = client.get(OWNERS_URL, headers=auth_headers(operator))
        assert resp.status_code == 403

    def test_cidr_match_allows_and_mismatch_denies(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "PLATFORM_IP_ALLOWLIST", "10.0.0.0/8")
        monkeypatch.setattr(settings, "RATE_LIMIT_TRUST_FORWARDED_FOR", True)
        operator = _seed_operator(db)

        allowed = client.get(
            OWNERS_URL,
            headers={**auth_headers(operator), "X-Forwarded-For": "10.1.2.3"},
        )
        assert allowed.status_code == 200

        denied = client.get(
            OWNERS_URL,
            headers={**auth_headers(operator), "X-Forwarded-For": "192.168.1.1"},
        )
        assert denied.status_code == 403

    def test_allowlist_covers_enrollment_surface_too(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "PLATFORM_IP_ALLOWLIST", "10.0.0.0/8")
        monkeypatch.setattr(settings, "RATE_LIMIT_TRUST_FORWARDED_FOR", True)
        operator = _seed_operator(db)
        token = create_access_token(subject=str(operator.id), extra={"mfa": "enroll"})
        resp = client.get(
            MFA_URL, headers={**_bearer(token), "X-Forwarded-For": "192.168.1.1"}
        )
        assert resp.status_code == 403

    def test_tenant_surface_unaffected_by_allowlist(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "PLATFORM_IP_ALLOWLIST", "10.0.0.0/8")
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        resp = client.get("/api/v1/customers", headers=auth_headers(admin))
        assert resp.status_code == 200

    @pytest.mark.no_db
    def test_malformed_allowlist_config_refuses_to_boot(self):
        from pydantic import ValidationError

        from app.lib.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                DATABASE_URL="postgresql://u:p@localhost:5432/x",
                JWT_SECRET_KEY="a-sufficiently-long-test-secret-key-123456",
                ENVIRONMENT="test",
                PLATFORM_IP_ALLOWLIST="10.0.0.0/8, not-a-network",
            )

    @pytest.mark.no_db
    def test_malformed_mfa_encryption_key_refuses_to_boot(self):
        from pydantic import ValidationError

        from app.lib.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                DATABASE_URL="postgresql://u:p@localhost:5432/x",
                JWT_SECRET_KEY="a-sufficiently-long-test-secret-key-123456",
                ENVIRONMENT="test",
                MFA_ENCRYPTION_KEY="too-short",
            )
