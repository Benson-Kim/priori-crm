"""Per-user behavioural baselines and the graduated risk model (issue #67).

Pins the critical design requirement: terminate only for genuinely
malicious signals, never hard-lock a legitimate user who shows up from a
new place, on a new device, at night, or during a busy minute.

- SOFT signals (new device, new country, unusual hour, mild volume
  deviation) carry low individual weight: alone they allow + log, in
  combination they reach at most an OTP step-up challenge — NEVER a
  termination (false-positive regression tests).
- HARD signals escalate directly: exfiltration-scale reads terminate the
  session; exhausting the OTP step-up budget terminates the challenged
  sessions it was trying to launder.
- A passed OTP step-up absorbs the verifying context into the user's
  baseline, so the same new device/place never re-fires (no repeated
  challenges for a context the user has already verified).
- Fail-safe degradation: no baseline history and no geo enrichment mean
  no signal — absence of data never reads as anomaly.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from jose import jwt

from app.common.audit import AuditEvent
from app.common.authz import baselines
from app.common.security import create_access_token, hash_password
from app.constants.enums import SessionStatus, UserRole
from app.lib.config import settings
from app.modules.auth.models import User, UserBaseline, UserSession

VALID_PASSWORD = "Str0ng!Passw0rd"

KE_GEO = {"X-Geo-Country": "KE", "X-Geo-Lat": "-1.286", "X-Geo-Lon": "36.817"}
US_GEO = {"X-Geo-Country": "US", "X-Geo-Lat": "40.712", "X-Geo-Lon": "-74.006"}

KNOWN_DEVICE = {"X-Device-Fingerprint": "known-laptop"}
NEW_DEVICE = {"X-Device-Fingerprint": "burner-phone"}


def _seed_user(db, email: str, role: UserRole = UserRole.MEMBER) -> User:
    user = User(
        email=email,
        password_hash=hash_password(VALID_PASSWORD),
        first_name="Baseline",
        last_name="Tester",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _local_hour(offset: int = 0) -> int:
    """The tenant-local hour, exactly as the gate computes it."""
    hour = datetime.now(UTC).astimezone(ZoneInfo(settings.REPORTING_TIMEZONE)).hour
    return (hour + offset) % 24


def _all_hours_except_now() -> dict:
    """An hour histogram where the CURRENT hour (and neighbours) is unseen.

    Deterministic at any wall-clock time: whatever hour the suite runs at,
    that hour and its neighbours read as never-observed while every other
    hour is habitual.
    """
    excluded = {_local_hour(-1), _local_hour(0), _local_hour(1)}
    return {str(h): 10 for h in range(24) if h not in excluded}


def _all_hours() -> dict:
    return {str(h): 10 for h in range(24)}


def _seed_baseline(
    db,
    user: User,
    *,
    devices: tuple = (),
    countries: tuple = (),
    hours: dict | None = None,
    volumes: dict | None = None,
) -> UserBaseline:
    hours = hours or {}
    baseline = UserBaseline(
        user_id=user.id,
        known_devices=list(devices),
        known_countries=list(countries),
        hour_counts=hours,
        hour_observations=sum(hours.values()),
        volume_baselines=volumes or {},
    )
    db.add(baseline)
    db.commit()
    return baseline


def _craft_session(db, user: User) -> tuple[UserSession, dict]:
    """A live session + bearer headers, minted WITHOUT the OTP flow.

    Models a hijacked/stale token presenting a session whose context was
    never absorbed into the baseline — the shape the session-start soft
    signals exist to catch.
    """
    session = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value)
    db.add(session)
    db.commit()
    token = create_access_token(
        subject=str(user.id),
        extra={"sid": str(session.id), "sua": int(datetime.now(UTC).timestamp())},
    )
    return session, {"Authorization": f"Bearer {token}"}


def _login_session(client, email: str, headers: dict | None = None) -> tuple[str, str]:
    """Drive the real login → OTP flow, with optional context headers."""
    headers = headers or {}
    with patch("app.modules.auth.service.AuthService._send_otp_email") as send:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": VALID_PASSWORD},
            headers=headers,
        )
        assert resp.status_code == 200
    code = send.call_args[0][1]
    resp = client.post(
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": code},
        headers=headers,
    )
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


def _soft_events(db, name: str) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "session",
            AuditEvent.action == f"soft_signal_{name}",
        )
        .all()
    )


@pytest.fixture
def trusted_ctx(monkeypatch):
    monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)


class TestBaselineAbsorption:
    def test_verify_otp_absorbs_device_country_and_hour(self, client, db, trusted_ctx):
        user = _seed_user(db, "absorb@mail.com")
        _login_session(client, "absorb@mail.com", headers={**KNOWN_DEVICE, **KE_GEO})

        baseline = db.get(UserBaseline, user.id)
        assert baseline is not None
        assert "client:known-laptop" in baseline.known_devices
        assert "KE" in baseline.known_countries
        assert baseline.hour_observations >= 1
        assert baseline.hour_counts.get(str(_local_hour()), 0) >= 1

    def test_absorption_is_idempotent(self, client, db, trusted_ctx):
        user = _seed_user(db, "again@mail.com")
        _login_session(client, "again@mail.com", headers={**KNOWN_DEVICE, **KE_GEO})
        _login_session(client, "again@mail.com", headers={**KNOWN_DEVICE, **KE_GEO})

        baseline = db.get(UserBaseline, user.id)
        assert baseline.known_devices.count("client:known-laptop") == 1
        assert baseline.known_countries.count("KE") == 1

    def test_passed_step_up_stops_repeat_challenges_for_same_context(
        self, client, db, trusted_ctx
    ):
        """The absorption contract end to end.

        A user with an established baseline shows up from a new device, a
        new country, at a never-seen hour: challenged (soft combination).
        They complete the login → OTP step-up FROM THAT CONTEXT. The same
        context must never challenge them again.
        """
        user = _seed_user(db, "mover@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours_except_now(),
        )

        _, headers = _craft_session(db, user)
        challenged = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **US_GEO}
        )
        assert challenged.status_code == 401
        assert challenged.json()["error_code"] == "STEP_UP_REQUIRED"

        # The user answers the challenge from the very context that fired.
        access, _ = _login_session(
            client, "mover@mail.com", headers={**NEW_DEVICE, **US_GEO}
        )
        ok = client.get(
            "/api/v1/customers", headers={**_bearer(access), **NEW_DEVICE, **US_GEO}
        )
        assert ok.status_code == 200
        session = db.get(UserSession, _sid(access))
        assert session.risk_score == 0, "absorbed context must not re-fire"

        baseline = db.get(UserBaseline, user.id)
        assert "client:burner-phone" in baseline.known_devices
        assert "US" in baseline.known_countries


class TestSoftSignalFalsePositives:
    """Benign-unusual shapes must never terminate a session."""

    def test_single_new_device_allows_and_logs(self, client, db, trusted_ctx):
        user = _seed_user(db, "newlaptop@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **KE_GEO}
        )
        assert resp.status_code == 200, "one soft signal must allow"
        assert session.risk_score == settings.RISK_SCORE_NEW_DEVICE
        events = _soft_events(db, "new_device")
        assert events and events[-1].after["points"] == settings.RISK_SCORE_NEW_DEVICE

    def test_new_device_and_country_still_allows(self, client, db, trusted_ctx):
        user = _seed_user(db, "traveller@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **US_GEO}
        )
        assert resp.status_code == 200, "two soft signals must still allow"
        assert (
            session.risk_score
            == settings.RISK_SCORE_NEW_DEVICE + settings.RISK_SCORE_NEW_COUNTRY
        )
        assert session.status == SessionStatus.ACTIVE.value

    def test_full_soft_combination_challenges_but_never_terminates(
        self, client, db, trusted_ctx
    ):
        """New device + new country + never-seen hour = step-up, NOT a kill."""
        user = _seed_user(db, "nightowl@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours_except_now(),
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **US_GEO}
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "STEP_UP_REQUIRED"
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value
        assert session.status != SessionStatus.TERMINATED.value
        assert session.risk_score < settings.RISK_TERMINATE_THRESHOLD, (
            "soft evidence alone must never reach the terminate threshold"
        )

        # And the follow-up is still a challenge, not a termination.
        again = client.get("/api/v1/customers", headers={**headers, **KE_GEO})
        assert again.status_code == 401
        assert again.json()["error_code"] == "STEP_UP_REQUIRED"

    def test_soft_only_terminate_crossing_clamps_to_challenge(
        self, client, db, trusted_ctx
    ):
        """Accumulated soft evidence can cross 100 across batches — clamp it.

        A session sitting just under the challenge line (softs + decay)
        plus one more soft batch (device change) can cross the terminate
        threshold with zero hard evidence: a new laptop, a new country,
        hours of work, then a browser auto-update in a busy minute.
        Termination requires a HARD signal; this must step up, never kill.
        """
        user = _seed_user(db, "unlucky@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        now = datetime.now(UTC)
        # Mid-session shape: start-softs already evaluated, device known,
        # accumulated soft score one point under the terminate line with a
        # fresh decay anchor.
        session.risk_score = settings.RISK_TERMINATE_THRESHOLD - 1
        session.risk_updated_at = now
        session.last_seen_at = now
        session.device_fingerprint = "client:known-laptop"
        db.commit()

        resp = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **KE_GEO}
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "STEP_UP_REQUIRED", (
            "a soft-only crossing of the terminate threshold must clamp "
            "to a challenge, never terminate"
        )
        db.refresh(session)
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value
        assert session.risk_score >= settings.RISK_TERMINATE_THRESHOLD

        challenged = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "session_challenged",
            )
            .all()
        )
        assert challenged
        assert challenged[-1].after.get("soft_clamp") is True

    def test_empty_baseline_fires_nothing(self, client, db, trusted_ctx):
        """No history = nothing to deviate from = no penalty (fail-safe)."""
        user = _seed_user(db, "firstday@mail.com")

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **US_GEO}
        )
        assert resp.status_code == 200
        assert session.risk_score == 0

    def test_missing_geo_enrichment_degrades_softly(self, client, db, trusted_ctx):
        """No geo signal must never fire the new-country soft signal."""
        user = _seed_user(db, "nogeo@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        resp = client.get("/api/v1/customers", headers={**headers, **KNOWN_DEVICE})
        assert resp.status_code == 200
        assert session.risk_score == 0
        assert not _soft_events(db, "new_country")


class TestUnusualHour:
    def test_unusual_hour_alone_only_logs(self, client, db, trusted_ctx):
        user = _seed_user(db, "latework@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours_except_now(),
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **KNOWN_DEVICE, **KE_GEO}
        )
        assert resp.status_code == 200, "an odd hour alone must allow"
        assert session.risk_score == settings.RISK_SCORE_UNUSUAL_HOUR
        assert _soft_events(db, "unusual_hour")

    def test_insufficient_history_never_fires_hour_signal(
        self, client, db, trusted_ctx
    ):
        user = _seed_user(db, "newbie@mail.com")
        # A handful of observations, all at some other hour: below the
        # min-sample gate, so "typical hours" do not exist yet.
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours={str(_local_hour(6)): 3},
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **KNOWN_DEVICE, **KE_GEO}
        )
        assert resp.status_code == 200
        assert session.risk_score == 0

    def test_adjacent_hour_is_not_unusual(self, client, db, trusted_ctx):
        """A habitual 9-to-5 worker starting 40 minutes early is not an anomaly."""
        user = _seed_user(db, "earlybird@mail.com")
        hours = {str(_local_hour(1)): 60}  # enough history, all next hour
        _seed_baseline(
            db, user, devices=("client:known-laptop",), countries=("KE",), hours=hours
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **KNOWN_DEVICE, **KE_GEO}
        )
        assert resp.status_code == 200
        assert session.risk_score == 0


class TestExfiltrationScaleReads:
    def test_exfil_burst_terminates_directly(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 2)
        monkeypatch.setattr(settings, "RISK_VOLUME_EXFIL_MULTIPLIER", 2)
        _seed_user(db, "vacuum@mail.com")
        access, _ = _login_session(client, "vacuum@mail.com")

        # Requests 1-2 clean; 3 crosses the mild (soft) ceiling: allow+log.
        for _ in range(2):
            assert (
                client.get("/api/v1/customers", headers=_bearer(access)).status_code
                == 200
            )
        mild = client.get("/api/v1/customers", headers=_bearer(access))
        assert mild.status_code == 200, "mild volume deviation is soft: allow + log"

        # Request 4 within the exfil ceiling (2*2=4); request 5 crosses it.
        assert (
            client.get("/api/v1/customers", headers=_bearer(access)).status_code == 200
        )
        killed = client.get("/api/v1/customers", headers=_bearer(access))
        assert killed.status_code == 401
        assert "terminated" in killed.json()["error"].lower()

        session = db.get(UserSession, _sid(access))
        assert session.status == SessionStatus.TERMINATED.value
        exfil_events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "exfiltration_volume",
            )
            .all()
        )
        assert exfil_events and exfil_events[-1].after["max_requests"] == 4

    def test_busy_minute_alone_never_challenges(self, client, db, monkeypatch):
        """Mild deviation at default thresholds: allow + log, nothing more."""
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 3)
        _seed_user(db, "busy@mail.com")
        access, _ = _login_session(client, "busy@mail.com")

        for _ in range(3):
            assert (
                client.get("/api/v1/customers", headers=_bearer(access)).status_code
                == 200
            )
        burst = client.get("/api/v1/customers", headers=_bearer(access))
        assert burst.status_code == 200, (
            "a mild volume deviation alone must not challenge"
        )
        session = db.get(UserSession, _sid(access))
        assert session.risk_score == settings.RISK_SCORE_VOLUME_ANOMALY
        assert session.status == SessionStatus.ACTIVE.value


class TestExfilEvasionPaths:
    """#67 review F8 + H11 + H12: the exfiltration ceiling cannot be dodged."""

    def test_rate_limited_hammering_still_terminates(self, client, db, monkeypatch):
        """F8 regression, with rate limiting ENABLED.

        The limiter rejects at RATE_LIMIT_PER_MINUTE before the gate runs,
        so the exfiltration detector never saw the hammering that proves
        exfiltration at scale. 429s now charge the same volume counters
        (and latch the crossing across the window boundary), so backing
        off after a rejected burst does not launder the evidence: the
        next SERVED request terminates the session.
        """
        # Exfil ceiling 13 * 5 = 65: above the limiter's 60/min, so only
        # the 429 evidence can cross it — without the F8 feed the served
        # requests alone (~61) never reach the ceiling.
        monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 13)
        monkeypatch.setattr(settings, "RISK_VOLUME_EXFIL_MULTIPLIER", 5)

        _seed_user(db, "hammer@mail.com")
        access, _ = _login_session(client, "hammer@mail.com")

        statuses = []
        for _ in range(75):
            statuses.append(
                client.get("/api/v1/customers", headers=_bearer(access)).status_code
            )
        assert 429 in statuses, "the limiter must actually engage"
        assert 401 not in statuses[: statuses.index(429)]

        # The attacker waits out the limiter window; the crossing latched.
        monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
        killed = client.get("/api/v1/customers", headers=_bearer(access))
        assert killed.status_code == 401
        assert "terminated" in killed.json()["error"].lower()

        session = db.get(UserSession, _sid(access))
        db.refresh(session)
        assert session.status == SessionStatus.TERMINATED.value

    def test_parallel_sessions_cannot_split_the_exfil_volume(
        self, client, db, monkeypatch
    ):
        """H11: volume is charged per USER as well as per session.

        N parallel logins split per-session counters N ways, each under
        its ceiling; the per-user aggregate window sums them back.
        """
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 4)
        monkeypatch.setattr(settings, "RISK_VOLUME_EXFIL_MULTIPLIER", 5)  # 20/user

        _seed_user(db, "splitter@mail.com")
        access_a, _ = _login_session(client, "splitter@mail.com")
        access_b, _ = _login_session(client, "splitter@mail.com")

        # Session A stays comfortably under its own exfil ceiling (12<20).
        for _ in range(12):
            resp = client.get("/api/v1/customers", headers=_bearer(access_a))
            assert resp.status_code == 200

        # Session B also stays under ITS ceiling — but the user aggregate
        # crosses, which is HARD evidence and terminates.
        statuses_b = []
        for _ in range(12):
            statuses_b.append(
                client.get("/api/v1/customers", headers=_bearer(access_b)).status_code
            )
        assert 401 in statuses_b

        session_b = db.get(UserSession, _sid(access_b))
        db.refresh(session_b)
        assert session_b.status == SessionStatus.TERMINATED.value

        exfil_events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "exfiltration_volume",
            )
            .all()
        )
        assert exfil_events and exfil_events[-1].after["scope"] == "user"

        # The sibling session dies on its next request too.
        assert (
            client.get("/api/v1/customers", headers=_bearer(access_a)).status_code
            == 401
        )

    def test_export_requests_are_weighted(self):
        """H12: export/bulk endpoints charge RISK_VOLUME_EXPORT_COST units."""
        from app.common.authz.risk import _request_cost

        assert _request_cost("/api/v1/reports/sales/export") == (
            settings.RISK_VOLUME_EXPORT_COST
        )
        assert _request_cost("/api/v1/sales-desk/exports/pipeline") == (
            settings.RISK_VOLUME_EXPORT_COST
        )
        assert _request_cost("/api/v1/customers") == 1
        # Similar-looking names never inherit the weight.
        assert _request_cost("/api/v1/exporters") == 1


class TestAdaptiveVolumeCeiling:
    def test_learned_typical_volume_lowers_the_mild_ceiling(
        self, client, db, monkeypatch
    ):
        """A user who typically reads 2/window is flagged (softly) at 8, not 300."""
        monkeypatch.setattr(settings, "RISK_VOLUME_MIN_CEILING", 5)
        user = _seed_user(db, "quiet@mail.com")
        access, _ = _login_session(client, "quiet@mail.com")

        baseline = db.get(UserBaseline, user.id)
        baseline.volume_baselines = {
            "internal": {
                "ewma": 2.0,
                "count": 0,
                "windows": 5,
                "window_started_at": datetime.now(UTC).isoformat(),
            }
        }
        db.commit()

        # Adaptive ceiling: clamp(2.0 * 4, 5, 300) = 8.
        for _ in range(8):
            assert (
                client.get("/api/v1/customers", headers=_bearer(access)).status_code
                == 200
            )
        ninth = client.get("/api/v1/customers", headers=_bearer(access))
        assert ninth.status_code == 200, "adaptive mild deviation is still soft"

        session = db.get(UserSession, _sid(access))
        assert session.risk_score == settings.RISK_SCORE_VOLUME_ANOMALY
        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "volume_anomaly",
            )
            .all()
        )
        assert events and events[-1].after["max_requests"] == 8
        assert events[-1].after["sensitivity_class"] == "internal"


class TestFailedStepUps:
    def test_exhausting_otp_budget_terminates_challenged_sessions_only(
        self, client, db, monkeypatch
    ):
        monkeypatch.setattr(settings, "AUTH_MAX_OTP_ATTEMPTS", 2)
        user = _seed_user(db, "burner@mail.com")

        challenged = UserSession(
            user_id=user.id, status=SessionStatus.CHALLENGE_REQUIRED.value
        )
        healthy = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value)
        db.add_all([challenged, healthy])
        db.commit()

        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": "burner@mail.com", "password": VALID_PASSWORD},
            )
            assert resp.status_code == 200

        for _ in range(2):
            resp = client.post(
                "/api/v1/auth/verify-otp",
                json={"email": "burner@mail.com", "code": "000000"},
            )
            assert resp.status_code == 401

        db.refresh(challenged)
        db.refresh(healthy)
        assert challenged.status == SessionStatus.TERMINATED.value
        assert challenged.termination_reason == "failed_step_up"
        # The victim's healthy session must survive: failed step-ups are a
        # signal about the CHALLENGED context, not a denial-of-service lever.
        assert healthy.status == SessionStatus.ACTIVE.value

    def test_wrong_code_below_budget_terminates_nothing(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MAX_OTP_ATTEMPTS", 3)
        user = _seed_user(db, "typo@mail.com")
        challenged = UserSession(
            user_id=user.id, status=SessionStatus.CHALLENGE_REQUIRED.value
        )
        db.add(challenged)
        db.commit()

        with patch("app.modules.auth.service.AuthService._send_otp_email"):
            client.post(
                "/api/v1/auth/login",
                json={"email": "typo@mail.com", "password": VALID_PASSWORD},
            )
        resp = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "typo@mail.com", "code": "000000"},
        )
        assert resp.status_code == 401

        db.refresh(challenged)
        assert challenged.status == SessionStatus.CHALLENGE_REQUIRED.value


class TestBaselineLearningUnits:
    """Pure(ish) unit checks of the learning helpers."""

    def test_volume_ceiling_unlearned_uses_global(self, db):
        user = _seed_user(db, "unit1@mail.com")
        baseline = _seed_baseline(db, user)
        assert (
            baselines.volume_ceiling(baseline, "internal")
            == settings.RISK_VOLUME_MAX_REQUESTS
        )

    def test_volume_ceiling_clamps_floor_and_global(self, db, monkeypatch):
        user = _seed_user(db, "unit2@mail.com")
        baseline = _seed_baseline(
            db,
            user,
            volumes={
                "internal": {
                    "ewma": 1.0,
                    "count": 0,
                    "windows": 5,
                    "window_started_at": None,
                },
                "confidential": {
                    "ewma": 1e6,
                    "count": 0,
                    "windows": 5,
                    "window_started_at": None,
                },
            },
        )
        assert (
            baselines.volume_ceiling(baseline, "internal")
            == settings.RISK_VOLUME_MIN_CEILING
        )
        assert (
            baselines.volume_ceiling(baseline, "confidential")
            == settings.RISK_VOLUME_MAX_REQUESTS
        )

    def test_known_device_list_is_capped(self, db):
        user = _seed_user(db, "unit3@mail.com")
        baseline = _seed_baseline(db, user)
        for i in range(20):
            baselines._remember(baseline.known_devices, f"client:dev-{i}", 8)
        assert len(baseline.known_devices) == 8
        assert baseline.known_devices[-1] == "client:dev-19"
        assert "client:dev-0" not in baseline.known_devices

    def test_hour_histogram_halves_at_cap(self, db):
        user = _seed_user(db, "unit4@mail.com")
        baseline = _seed_baseline(db, user, hours={"9": 4998, "23": 1})
        baselines._bump_hour(baseline, 9)  # reaches the 5000 cap
        assert baseline.hour_counts["9"] == 2499
        assert "23" not in baseline.hour_counts  # 1 // 2 == 0: pruned
        assert baseline.hour_observations == 2499
