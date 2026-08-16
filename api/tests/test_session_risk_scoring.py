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
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from jose import jwt

from app.common.audit import AuditEvent
from app.common.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
)
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

    def test_stale_geo_fix_uses_its_own_anchor_not_last_seen(
        self, client, db, trusted_geo
    ):
        """Non-geo requests between two fixes must not inflate the speed.

        Elapsed time anchors on when the stored coordinates were CAPTURED
        (``last_geo_at``), not on ``last_seen_at`` — which every request
        updates, so a fresh non-geolocated request would otherwise make a
        genuine 14-hour flight read as teleportation (+70, instant
        challenge) purely because geo coverage was intermittent.
        """
        _seed_user(db, "flyer@mail.com")
        access, _ = _login_session(client, "flyer@mail.com")

        first = client.get("/api/v1/customers", headers={**_bearer(access), **NAIROBI})
        assert first.status_code == 200

        # The user flies for ~14h; a non-geolocated request kept
        # last_seen_at fresh while the Nairobi fix aged.
        session = db.get(UserSession, _sid(access))
        session.last_geo_at = datetime.now(UTC) - timedelta(hours=14)
        db.commit()

        # New York is ~11,740 km away: 14h implies ~840 km/h — an
        # airliner, not teleportation.
        second = client.get(
            "/api/v1/customers", headers={**_bearer(access), **NEW_YORK}
        )
        assert second.status_code == 200
        db.refresh(session)
        assert session.risk_score == 0
        assert not _session_events(db, "impossible_travel")

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
        assert events and events[-1].after["max_requests"] == 3

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


class TestScoreDecay:
    """Benign noise must not accumulate into a false challenge.

    Without decay the score is a ratchet: a browser auto-update (+25), one
    busy minute (+30) and a stray 403 (+25) put ANY long-lived session past
    the 60-point threshold, and the user is challenged for doing nothing.
    """

    def test_old_points_decay_below_the_threshold(self, client, db):
        _seed_user(db, "veteran@mail.com")
        access, _ = _login_session(client, "veteran@mail.com")
        session = db.get(UserSession, _sid(access))

        # 80 points earned over a working day, last anomaly 9 hours ago.
        session.risk_score = 80
        session.risk_updated_at = datetime.now(UTC) - timedelta(hours=9)
        db.commit()

        resp = client.get("/api/v1/customers", headers=_bearer(access))
        assert resp.status_code == 200, "decayed noise must not challenge"

    def test_recent_points_still_challenge(self, client, db):
        """Decay must not blunt a burst that just happened."""
        _seed_user(db, "attacker@mail.com")
        access, _ = _login_session(client, "attacker@mail.com")
        session = db.get(UserSession, _sid(access))

        session.risk_score = 80
        session.risk_updated_at = datetime.now(UTC) - timedelta(minutes=2)
        db.commit()

        resp = client.get("/api/v1/customers", headers=_bearer(access))
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "STEP_UP_REQUIRED"

    def test_challenged_status_never_decays_back_to_active(self, client, db):
        """The status is sticky: only re-authentication restores trust."""
        _seed_user(db, "flagged@mail.com")
        access, _ = _login_session(client, "flagged@mail.com")
        session = db.get(UserSession, _sid(access))

        session.status = SessionStatus.CHALLENGE_REQUIRED.value
        session.risk_score = 80
        # Long enough ago that the SCORE would decay to zero.
        session.risk_updated_at = datetime.now(UTC) - timedelta(days=3)
        db.commit()

        resp = client.get("/api/v1/customers", headers=_bearer(access))
        assert resp.status_code == 401
        db.refresh(session)
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value

    def test_decision_audit_records_the_score_it_acted_on(self, client, db):
        """A row saying 80 for a decision taken on a decayed value is a lie."""
        _seed_user(db, "auditee@mail.com")
        access, _ = _login_session(client, "auditee@mail.com")
        session = db.get(UserSession, _sid(access))
        session.risk_score = 80
        session.risk_updated_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

        assert (
            client.get("/api/v1/customers", headers=_bearer(access)).status_code == 401
        )

        event = _session_events(db, "session_challenged")[-1]
        assert event.after["risk_score"] == 80
        assert event.after["effective_score"] == 80
        assert event.after["decay_per_hour"] == settings.RISK_DECAY_PER_HOUR


class TestSessionLifetime:
    def test_session_past_max_age_is_terminated(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "SESSION_MAX_AGE_HOURS", 1)
        _seed_user(db, "ancient@mail.com")
        access, _ = _login_session(client, "ancient@mail.com")
        session = db.get(UserSession, _sid(access))
        session.created_at = datetime.now(UTC) - timedelta(hours=5)
        db.commit()

        assert (
            client.get("/api/v1/customers", headers=_bearer(access)).status_code == 401
        )
        db.refresh(session)
        assert session.status == SessionStatus.TERMINATED.value
        # Distinct from a risk kill, so the trail stays readable.
        assert session.termination_reason == "max session age exceeded"

    def test_idle_session_is_terminated(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30)
        _seed_user(db, "afk@mail.com")
        access, _ = _login_session(client, "afk@mail.com")
        session = db.get(UserSession, _sid(access))
        session.last_seen_at = datetime.now(UTC) - timedelta(hours=4)
        db.commit()

        assert (
            client.get("/api/v1/customers", headers=_bearer(access)).status_code == 401
        )
        db.refresh(session)
        assert session.status == SessionStatus.TERMINATED.value
        assert session.termination_reason == "session idle timeout"

    def test_active_session_within_limits_is_untouched(self, client, db):
        _seed_user(db, "working@mail.com")
        access, _ = _login_session(client, "working@mail.com")

        assert (
            client.get("/api/v1/customers", headers=_bearer(access)).status_code == 200
        )
        session = db.get(UserSession, _sid(access))
        assert session.status == SessionStatus.ACTIVE.value


class TestRiskEvidenceIsDurable:
    """A score bump must outlive the rejection it causes.

    The route class commits only when the handler RETURNS; an exception
    propagates straight past it. Anything relying on that commit vanishes on
    the very requests an attacker is generating, which makes probing free.
    """

    def test_volume_window_survives_failing_requests(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 3)
        monkeypatch.setattr(settings, "RISK_CHALLENGE_THRESHOLD", 30)
        _seed_user(db, "prober2@mail.com")
        access, _ = _login_session(client, "prober2@mail.com")

        # Burn the window on requests that all 404 — every one of these rolls
        # the request transaction back.
        for _ in range(3):
            assert (
                client.get(
                    f"/api/v1/customers/{uuid.uuid4()}", headers=_bearer(access)
                ).status_code
                == 404
            )

        # The counter still remembers them, so the next request crosses.
        blocked = client.get("/api/v1/customers", headers=_bearer(access))
        assert blocked.status_code == 401
        assert blocked.json()["error_code"] == "STEP_UP_REQUIRED"


class TestConcurrentScoringAtomicity:
    """#67 review F1: risk mutations must not be last-writer-wins.

    The session row is loaded under SELECT ... FOR UPDATE before the
    detectors read ``risk_score``, so concurrent requests of one session
    serialize their read-modify-write instead of committing independently
    computed replacements — an attacker firing several anomalies in
    parallel would otherwise keep only ONE increment and stay below the
    challenge/terminate thresholds. Mirrors the M3 fix in
    note_privilege_escalation. SQLite ignores FOR UPDATE; CI runs the
    suite on Postgres, where the lock is real and this load serializes.
    """

    def test_gate_loads_the_session_row_locked(self, client, db, monkeypatch):
        _seed_user(db, "parallel@mail.com")
        access, _ = _login_session(client, "parallel@mail.com")

        from sqlalchemy.orm import Session as SaSession

        lock_loads: list[bool] = []
        original_get = SaSession.get

        def spy(self, entity, ident, **kw):
            if getattr(entity, "__name__", "") == "UserSession":
                lock_loads.append(bool(kw.get("with_for_update")))
            return original_get(self, entity, ident, **kw)

        monkeypatch.setattr(SaSession, "get", spy)
        resp = client.get("/api/v1/customers", headers=_bearer(access))
        assert resp.status_code == 200

        # The gate's load — the first UserSession read of the request —
        # must carry the row lock.
        assert lock_loads and lock_loads[0] is True


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

    def test_escalation_crossing_terminates_in_the_same_request(
        self, client, db, monkeypatch
    ):
        """#67 review F4: the 403 that crosses the line acts immediately.

        note_privilege_escalation used to add points and commit WITHOUT
        evaluating the thresholds: the crossing completed an ordinary 403
        and the next request saw the score with no current hard signal —
        clamped to a challenge instead of the direct termination the
        taxonomy documents for hard evidence.

        Pinned semantics (ADR-0012): the crossing request KEEPS its RBAC
        403 — the request was already denied, and rewriting the in-flight
        response would obscure the RBAC answer without denying anything
        more. The termination and its audit evidence commit atomically
        with the increment, so every SUBSEQUENT request 401s.

        Scenario note: the entry score must sit below the challenge
        threshold (60) or the zero-trust gate challenges the request
        before RBAC ever runs; from there the default +25 cannot reach
        the terminate line (100), so the escalation weight is raised for
        the test. What is under test is the crossing, not the weight.
        """
        monkeypatch.setattr(settings, "RISK_SCORE_PRIVILEGE_ESCALATION", 60)
        _seed_user(db, "escalator@mail.com", role=UserRole.MEMBER)
        access, _ = _login_session(client, "escalator@mail.com")

        session = db.get(UserSession, _sid(access))
        session.risk_score = 45  # gate admits (<60); 45 + 60 crosses 100
        session.risk_updated_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

        resp = client.delete(
            f"/api/v1/invoices/{uuid.uuid4()}", headers=_bearer(access)
        )
        assert resp.status_code == 403

        db.refresh(session)
        assert session.status == SessionStatus.TERMINATED.value
        assert "escalation" in session.termination_reason
        assert _session_events(db, "session_terminated")

        dead = client.get("/api/v1/customers", headers=_bearer(access))
        assert dead.status_code == 401

    def test_escalation_crossing_challenges_in_the_same_request(self, client, db):
        """A challenge-line crossing transitions the status atomically too."""
        _seed_user(db, "prober3@mail.com", role=UserRole.MEMBER)
        access, _ = _login_session(client, "prober3@mail.com")

        session = db.get(UserSession, _sid(access))
        session.risk_score = 40
        session.risk_updated_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

        resp = client.delete(
            f"/api/v1/invoices/{uuid.uuid4()}", headers=_bearer(access)
        )
        assert resp.status_code == 403

        db.refresh(session)
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value

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

    def test_refresh_reuse_terminates_the_session_and_kills_access_tokens(
        self, client, db, monkeypatch
    ):
        """#67 review F5: reuse fences the family AND kills the session.

        Fencing alone left the session row ACTIVE, so already-issued
        access tokens for the compromised session kept working until
        natural expiry. Reuse now terminates the sid's session and pushes
        it onto the shared denylist, which the access-token validation
        path itself checks — proven below with the gate's own session
        re-check disabled.
        """
        _seed_user(db, "stolen@mail.com")
        access, refresh = _login_session(client, "stolen@mail.com")

        # Legitimate rotation spends the presented refresh token.
        rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert rotated.status_code == 200
        new_access = rotated.json()["access_token"]
        new_refresh = rotated.json()["refresh_token"]

        # A second presenter of the SPENT token is theft evidence.
        reused = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert reused.status_code == 401

        session = db.get(UserSession, _sid(access))
        db.refresh(session)
        assert session.status == SessionStatus.TERMINATED.value
        assert session.termination_reason == "refresh_token_reuse"

        # The descendant refresh token dies with the terminated session.
        descendant = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": new_refresh}
        )
        assert descendant.status_code == 401

        # Both access tokens carry the terminated sid and die IMMEDIATELY
        # on the token-validation path — even with ABAC (and its session
        # re-check) switched off entirely.
        monkeypatch.setattr(settings, "ABAC_ENABLED", False)
        for token in (access, new_access):
            dead = client.get("/api/v1/customers", headers=_bearer(token))
            assert dead.status_code == 401

    def test_logout_kills_access_tokens_on_the_validation_path(
        self, client, db, monkeypatch
    ):
        """The sid denylist holds even where the gate does not run (F5)."""
        _seed_user(db, "walker@mail.com")
        access, refresh = _login_session(client, "walker@mail.com")
        out = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
        assert out.status_code == 200

        monkeypatch.setattr(settings, "ABAC_ENABLED", False)
        dead = client.get("/api/v1/customers", headers=_bearer(access))
        assert dead.status_code == 401

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


class TestChallengeFenceIntegrity:
    """#67 review H15: a CHALLENGED session is fail-closed everywhere.

    The gate re-scores before ANY handler runs (app-level dependency; no
    websockets, mounts or static routes exist to slip past it), so a
    challenged session must be refused CONFIDENTIAL/RESTRICTED reads and
    all writes until the step-up passes.
    """

    def test_challenged_session_cannot_read_confidential(self, client, db):
        _seed_user(db, "fenced@mail.com", role=UserRole.ADMIN)
        access, _ = _login_session(client, "fenced@mail.com")

        session = db.get(UserSession, _sid(access))
        session.status = SessionStatus.CHALLENGE_REQUIRED.value
        db.commit()

        # CONFIDENTIAL read: refused with the step-up contract.
        read = client.get("/api/v1/invoices", headers=_bearer(access))
        assert read.status_code == 401
        assert read.json()["error_code"] == "STEP_UP_REQUIRED"

        # RESTRICTED read and an INTERNAL write: equally fenced.
        assert client.get("/api/v1/owner", headers=_bearer(access)).status_code == 401
        write = client.post("/api/v1/customers", json={}, headers=_bearer(access))
        assert write.status_code == 401


class TestTokenEdgeCases:
    def test_legacy_token_without_sid_still_works(self, client, db):
        user = _seed_user(db, "legacy@mail.com")
        token = create_access_token(subject=str(user.id))
        resp = client.get("/api/v1/customers", headers=_bearer(token))
        assert resp.status_code == 200

    def test_legacy_refresh_token_without_sid_is_refused(self, client, db):
        """#67 review F7: a sessionless refresh chain must not be immortal.

        A pre-deployment refresh token carries no sid; rotating it minted
        another sessionless pair with a fresh full lifetime, forever —
        and assess_session_risk skipped every one of them. The rotation
        is refused instead: one full re-login mints a tracked session.
        (The legacy ACCESS token keeps working for its remaining minutes
        — pinned by test_legacy_token_without_sid_still_works above.)
        """
        user = _seed_user(db, "legacyrefresh@mail.com")
        legacy_refresh, _, _ = create_refresh_token(subject=str(user.id))

        resp = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": legacy_refresh}
        )
        assert resp.status_code == 401
        # The refusal names its real cause: nothing was revoked, so the
        # revoked-token message would send the user hunting for a theft
        # that never happened.
        assert "legacy" in resp.json()["error"].lower()

    def test_unknown_session_id_is_refused(self, client, db):
        user = _seed_user(db, "phantom@mail.com")
        token = create_access_token(
            subject=str(user.id), extra={"sid": str(uuid.uuid4())}
        )
        resp = client.get("/api/v1/customers", headers=_bearer(token))
        assert resp.status_code == 401
        assert "terminated" in resp.json()["error"].lower()

    def test_password_reset_terminates_sessions(self, client, db):
        """#67 H16: a reset kills EVERY session, and none can refresh."""
        _seed_user(db, "resetter@mail.com")
        access, _ = _login_session(client, "resetter@mail.com")
        access_2, refresh_2 = _login_session(client, "resetter@mail.com")
        assert (
            client.get("/api/v1/customers", headers=_bearer(access)).status_code == 200
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

        # EVERY session dies, not just the newest.
        session_2 = db.get(UserSession, _sid(access_2))
        assert session_2.status == SessionStatus.TERMINATED.value

        dead = client.get("/api/v1/customers", headers=_bearer(access))
        assert dead.status_code == 401
        assert (
            client.get("/api/v1/customers", headers=_bearer(access_2)).status_code
            == 401
        )

        # A terminated session cannot refresh its way back to life.
        refreshed = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_2}
        )
        assert refreshed.status_code == 401
