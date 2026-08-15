"""End-to-end tests for continuous session risk scoring (issue #67).

Sessions are minted by the real login → OTP flow (their id travels as the
``sid`` claim in both JWTs) and re-scored on every request by the
zero-trust gate. These tests pin:

- impossible travel: two geolocated requests implying an implausible
  speed force a step-up challenge — or termination past the higher
  threshold — and both survive as audit rows;
- unusual data-access volume: exceeding the per-session request ceiling
  inside the rolling window forces a step-up;
- privilege-escalation attempts: RBAC 403s accumulate on the session and
  force a step-up, and the attempts are audited durably despite the 403
  rollback;
- the step-up contract: a challenged session is never cleared in place —
  re-running login → OTP mints a FRESH session whose tokens work, while
  the challenged session's tokens stay challenged;
- terminated sessions are dead everywhere: API requests, token refresh —
  and logout / password reset terminate sessions outright;
- legacy tokens without a ``sid`` claim keep working (no session to
  score), while a signed token naming an unknown session is refused.
"""

import uuid
from unittest.mock import patch

import pytest
from jose import jwt

from app.common.audit import AuditEvent
from app.common.security import create_access_token, hash_password
from app.constants.enums import SessionStatus, UserRole
from app.lib.config import settings
from app.modules.auth.models import User, UserSession

VALID_PASSWORD = "Str0ng!Pass"

# Geo fixtures: Nairobi and New York are ~11,740 km apart, so back-to-back
# requests imply a speed far beyond any airliner.
NAIROBI = {"X-Geo-Country": "KE", "X-Geo-Lat": "-1.286", "X-Geo-Lon": "36.817"}
NEW_YORK = {"X-Geo-Country": "US", "X-Geo-Lat": "40.712", "X-Geo-Lon": "-74.006"}


def _seed_user(db, email: str, role: UserRole = UserRole.MEMBER) -> User:
    user = User(
        email=email,
        password_hash=hash_password(VALID_PASSWORD),
        first_name="Risk",
        last_name="Tester",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_session(client, email: str) -> tuple[str, str]:
    """Drive the real login → OTP flow; return (access_token, refresh_token)."""
    with patch("app.modules.auth.service.AuthService._send_otp_email") as send:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": VALID_PASSWORD},
        )
        assert resp.status_code == 200
    code = send.call_args[0][1]
    resp = client.post("/api/v1/auth/verify-otp", json={"email": email, "code": code})
    assert resp.status_code == 200
    body = resp.json()
    return body["access_token"], body["refresh_token"]


def _sid(token: str) -> uuid.UUID:
    payload = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    return uuid.UUID(payload["sid"])


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def trusted_geo(monkeypatch):
    """Trust the edge context headers so tests can inject geolocation."""
    monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)


def _session_events(db, action: str) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_type == "session", AuditEvent.action == action)
        .all()
    )


class TestSessionMinting:
    def test_verify_otp_mints_session_and_sid_claims(self, client, db):
        user = _seed_user(db, "mint@mail.com")
        access, refresh = _login_session(client, "mint@mail.com")

        access_sid = _sid(access)
        refresh_sid = _sid(refresh)
        assert access_sid == refresh_sid

        session = db.get(UserSession, access_sid)
        assert session is not None
        assert session.user_id == user.id
        assert session.status == SessionStatus.ACTIVE.value
        assert session.risk_score == 0

    def test_second_login_mints_a_distinct_session(self, client, db):
        _seed_user(db, "mint@mail.com")
        first, _ = _login_session(client, "mint@mail.com")
        second, _ = _login_session(client, "mint@mail.com")
        assert _sid(first) != _sid(second)


class TestImpossibleTravel:
    def test_impossible_travel_forces_step_up(self, client, db, trusted_geo):
        _seed_user(db, "traveler@mail.com")
        access, _ = _login_session(client, "traveler@mail.com")

        ok = client.get("/api/v1/customers", headers={**_bearer(access), **NAIROBI})
        assert ok.status_code == 200

        challenged = client.get(
            "/api/v1/customers", headers={**_bearer(access), **NEW_YORK}
        )
        assert challenged.status_code == 401
        body = challenged.json()
        assert body["error_code"] == "STEP_UP_REQUIRED"
        assert body["details"]["challenge"] == "otp"

        session = db.get(UserSession, _sid(access))
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value
        assert session.risk_score >= settings.RISK_SCORE_IMPOSSIBLE_TRAVEL

        events = _session_events(db, "impossible_travel")
        assert events and events[-1].after["speed_kmh"] > 900

    def test_plausible_movement_is_clean(self, client, db, trusted_geo):
        _seed_user(db, "commuter@mail.com")
        access, _ = _login_session(client, "commuter@mail.com")

        nearby = {"X-Geo-Country": "KE", "X-Geo-Lat": "-1.30", "X-Geo-Lon": "36.82"}
        first = client.get("/api/v1/customers", headers={**_bearer(access), **NAIROBI})
        second = client.get("/api/v1/customers", headers={**_bearer(access), **nearby})
        assert first.status_code == 200
        assert second.status_code == 200
        assert db.get(UserSession, _sid(access)).risk_score == 0

    def test_terminate_threshold_kills_the_session(
        self, client, db, trusted_geo, monkeypatch
    ):
        monkeypatch.setattr(settings, "RISK_TERMINATE_THRESHOLD", 50)
        _seed_user(db, "victim@mail.com")
        access, _ = _login_session(client, "victim@mail.com")

        client.get("/api/v1/customers", headers={**_bearer(access), **NAIROBI})
        killed = client.get(
            "/api/v1/customers", headers={**_bearer(access), **NEW_YORK}
        )
        assert killed.status_code == 401
        assert "terminated" in killed.json()["error"].lower()

        session = db.get(UserSession, _sid(access))
        assert session.status == SessionStatus.TERMINATED.value

        # The session is dead everywhere: even a clean follow-up request
        # from the original location is refused.
        after = client.get("/api/v1/customers", headers={**_bearer(access), **NAIROBI})
        assert after.status_code == 401
        assert _session_events(db, "session_terminated")

    def test_step_up_mints_fresh_session_old_one_stays_challenged(
        self, client, db, trusted_geo
    ):
        _seed_user(db, "traveler@mail.com")
        access, _ = _login_session(client, "traveler@mail.com")

        client.get("/api/v1/customers", headers={**_bearer(access), **NAIROBI})
        challenged = client.get(
            "/api/v1/customers", headers={**_bearer(access), **NEW_YORK}
        )
        assert challenged.status_code == 401

        # Step up: the existing login → OTP flow mints a fresh session.
        fresh_access, _ = _login_session(client, "traveler@mail.com")
        assert _sid(fresh_access) != _sid(access)
        ok = client.get(
            "/api/v1/customers", headers={**_bearer(fresh_access), **NEW_YORK}
        )
        assert ok.status_code == 200

        # The challenged session is never cleared in place.
        stale = client.get("/api/v1/customers", headers={**_bearer(access), **NAIROBI})
        assert stale.status_code == 401
        assert stale.json()["error_code"] == "STEP_UP_REQUIRED"


class TestVolumeAnomaly:
    def test_exceeding_volume_ceiling_forces_step_up(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 3)
        monkeypatch.setattr(settings, "RISK_CHALLENGE_THRESHOLD", 30)
        _seed_user(db, "hoover@mail.com")
        access, _ = _login_session(client, "hoover@mail.com")

        for _ in range(3):
            assert (
                client.get("/api/v1/customers", headers=_bearer(access)).status_code
                == 200
            )

        burst = client.get("/api/v1/customers", headers=_bearer(access))
        assert burst.status_code == 401
        assert burst.json()["error_code"] == "STEP_UP_REQUIRED"

        session = db.get(UserSession, _sid(access))
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value
        events = _session_events(db, "volume_anomaly")
        assert events and events[-1].after["request_count"] == 4

    def test_volume_under_ceiling_is_clean(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 10)
        _seed_user(db, "reader@mail.com")
        access, _ = _login_session(client, "reader@mail.com")

        for _ in range(5):
            assert (
                client.get("/api/v1/customers", headers=_bearer(access)).status_code
                == 200
            )
        assert db.get(UserSession, _sid(access)).risk_score == 0


class TestPrivilegeEscalation:
    def test_rbac_rejections_accumulate_and_force_step_up(
        self, client, db, monkeypatch
    ):
        monkeypatch.setattr(settings, "RISK_CHALLENGE_THRESHOLD", 50)
        _seed_user(db, "prober@mail.com", role=UserRole.MEMBER)
        access, _ = _login_session(client, "prober@mail.com")

        # A member probing privileged endpoints: each RBAC 403 is scored.
        for _ in range(2):
            resp = client.delete(
                f"/api/v1/invoices/{uuid.uuid4()}", headers=_bearer(access)
            )
            assert resp.status_code == 403

        session = db.get(UserSession, _sid(access))
        assert session.escalation_count == 2
        assert session.risk_score >= 50
        assert len(_session_events(db, "privilege_escalation")) == 2

        # The next request — ANY request — is challenged.
        challenged = client.get("/api/v1/customers", headers=_bearer(access))
        assert challenged.status_code == 401
        assert challenged.json()["error_code"] == "STEP_UP_REQUIRED"

    def test_single_rejection_does_not_challenge(self, client, db):
        _seed_user(db, "curious@mail.com", role=UserRole.MEMBER)
        access, _ = _login_session(client, "curious@mail.com")

        resp = client.delete(
            f"/api/v1/invoices/{uuid.uuid4()}", headers=_bearer(access)
        )
        assert resp.status_code == 403

        # One attempt (25) stays below the default challenge threshold (60).
        ok = client.get("/api/v1/customers", headers=_bearer(access))
        assert ok.status_code == 200
        assert db.get(UserSession, _sid(access)).escalation_count == 1


class TestRefreshAndLogout:
    def test_refresh_preserves_the_session_identity(self, client, db):
        _seed_user(db, "rotator@mail.com")
        access, refresh = _login_session(client, "rotator@mail.com")

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert resp.status_code == 200
        body = resp.json()
        assert _sid(body["access_token"]) == _sid(access)
        assert _sid(body["refresh_token"]) == _sid(access)

    def test_refresh_refused_for_terminated_session(self, client, db):
        _seed_user(db, "gone@mail.com")
        _, refresh = _login_session(client, "gone@mail.com")

        session = db.get(UserSession, _sid(refresh))
        session.status = SessionStatus.TERMINATED.value
        db.commit()

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert resp.status_code == 401

    def test_refresh_cannot_launder_a_challenged_session(self, client, db):
        _seed_user(db, "challenged@mail.com")
        _, refresh = _login_session(client, "challenged@mail.com")

        session = db.get(UserSession, _sid(refresh))
        session.status = SessionStatus.CHALLENGE_REQUIRED.value
        db.commit()

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "STEP_UP_REQUIRED"

    def test_logout_terminates_the_session_and_kills_access_tokens(self, client, db):
        _seed_user(db, "leaver@mail.com")
        access, refresh = _login_session(client, "leaver@mail.com")

        ok = client.get("/api/v1/customers", headers=_bearer(access))
        assert ok.status_code == 200

        out = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
        assert out.status_code == 200

        session = db.get(UserSession, _sid(access))
        assert session.status == SessionStatus.TERMINATED.value
        assert session.termination_reason == "logout"

        # Zero trust: the still-unexpired ACCESS token is dead too.
        dead = client.get("/api/v1/customers", headers=_bearer(access))
        assert dead.status_code == 401


class TestTokenEdgeCases:
    def test_legacy_token_without_sid_still_works(self, client, db):
        user = _seed_user(db, "legacy@mail.com")
        token = create_access_token(subject=str(user.id))
        resp = client.get("/api/v1/customers", headers=_bearer(token))
        assert resp.status_code == 200

    def test_unknown_session_id_is_refused(self, client, db):
        user = _seed_user(db, "phantom@mail.com")
        token = create_access_token(
            subject=str(user.id), extra={"sid": str(uuid.uuid4())}
        )
        resp = client.get("/api/v1/customers", headers=_bearer(token))
        assert resp.status_code == 401
        assert "terminated" in resp.json()["error"].lower()

    def test_password_reset_terminates_sessions(self, client, db):
        _seed_user(db, "resetter@mail.com")
        access, _ = _login_session(client, "resetter@mail.com")
        assert (
            client.get("/api/v1/customers", headers=_bearer(access)).status_code
            == 200
        )

        with patch(
            "app.modules.auth.service.AuthService._send_password_reset_email"
        ) as send:
            resp = client.post(
                "/api/v1/auth/forgot-password", json={"email": "resetter@mail.com"}
            )
            assert resp.status_code == 200
        raw_token = send.call_args[0][1]

        resp = client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "N3w!Passw0rd"},
        )
        assert resp.status_code == 200

        session = db.get(UserSession, _sid(access))
        assert session.status == SessionStatus.TERMINATED.value
        assert session.termination_reason == "password_reset"

        dead = client.get("/api/v1/customers", headers=_bearer(access))
        assert dead.status_code == 401
