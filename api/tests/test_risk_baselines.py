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

import hashlib
import uuid
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

# Timeline fixtures anchor on the pinned instant the gate evaluates at
# (conftest's `pinned_abac_clock`), not on the real wall clock.
from tests.conftest import ABAC_EVALUATION_TIME

VALID_PASSWORD = "Str0ng!Passw0rd"

KE_GEO = {"X-Geo-Country": "KE", "X-Geo-Lat": "-1.286", "X-Geo-Lon": "36.817"}
US_GEO = {"X-Geo-Country": "US", "X-Geo-Lat": "40.712", "X-Geo-Lon": "-74.006"}

KNOWN_DEVICE = {"X-Device-Fingerprint": "known-laptop"}
NEW_DEVICE = {"X-Device-Fingerprint": "burner-phone"}

# The server-DERIVED fingerprint every TestClient request carries
# (User-Agent "testclient", no Accept-Language) — mirrors
# context._derived_fingerprint. Since issue #83 a client-supplied
# fingerprint is corroborating-only: a `client:` baseline match suppresses
# the new-device signal only when the derived form is ALSO known, so
# baselines modelling "this browser passed a step-up" must seed both —
# exactly what absorb_context records post-#83.
TESTCLIENT_DERIVED_FP = "derived:" + hashlib.sha256(b"testclient\n").hexdigest()[:32]


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
    """The tenant-local hour, exactly as the gate computes it.

    Anchored on the suite's pinned evaluation instant (conftest's
    `pinned_abac_clock`), which is what the gate reads — not the real
    wall clock, which it no longer does.
    """
    hour = ABAC_EVALUATION_TIME.astimezone(ZoneInfo(settings.REPORTING_TIMEZONE)).hour
    return (hour + offset) % 24


def _all_hours_except_now() -> dict:
    """An hour histogram where the EVALUATION hour (and neighbours) is unseen.

    Whatever wall-clock time the suite runs at, the gate evaluates at the
    pinned instant: that hour and its neighbours read as never-observed
    while every other hour is habitual.
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
        extra={
            "sid": str(session.id),
            # Fresh relative to the pinned evaluation clock the gate reads.
            "sua": int(ABAC_EVALUATION_TIME.timestamp()),
        },
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
        assert "client:known-laptop" in baselines.entry_values(baseline.known_devices)
        assert "KE" in baselines.entry_values(baseline.known_countries)
        assert baseline.hour_observations >= 1
        assert baseline.hour_counts.get(str(_local_hour()), 0) >= 1
        # Entries carry a verified_at stamp (#67 line review §4) so
        # absorbed trust can age out instead of being permanent.
        assert all(
            isinstance(entry, dict) and entry.get("verified_at")
            for entry in baseline.known_devices + baseline.known_countries
        )

    def test_absorption_is_idempotent(self, client, db, trusted_ctx):
        user = _seed_user(db, "again@mail.com")
        _login_session(client, "again@mail.com", headers={**KNOWN_DEVICE, **KE_GEO})
        _login_session(client, "again@mail.com", headers={**KNOWN_DEVICE, **KE_GEO})

        baseline = db.get(UserBaseline, user.id)
        devices = baselines.entry_values(baseline.known_devices)
        countries = baselines.entry_values(baseline.known_countries)
        assert devices.count("client:known-laptop") == 1
        assert countries.count("KE") == 1

    def test_absorption_is_audited_and_new_device_notifies(
        self, client, db, trusted_ctx
    ):
        """#67 H13: permanent trust entering the baseline leaves a trace.

        Every absorption writes a ``baseline_absorbed`` audit event; a
        NEW device additionally notifies the account owner — a
        compromised inbox is exactly where that mail is the only tell.
        """
        _seed_user(db, "traced@mail.com")
        with patch(
            "app.modules.auth.service.AuthService._send_new_device_alert"
        ) as alert:
            _login_session(client, "traced@mail.com", headers={**NEW_DEVICE, **US_GEO})
        alert.assert_called_once()

        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "baseline",
                AuditEvent.action == "baseline_absorbed",
            )
            .all()
        )
        assert events
        assert events[-1].after["new_device"] is True
        assert events[-1].after["country"] == "US"

        # A re-login from the SAME (now known) device does not re-alert.
        with patch(
            "app.modules.auth.service.AuthService._send_new_device_alert"
        ) as alert:
            _login_session(client, "traced@mail.com", headers={**NEW_DEVICE, **US_GEO})
        alert.assert_not_called()

    def test_new_country_absorption_on_known_fingerprint_notifies(
        self, client, db, trusted_ctx
    ):
        """#67 line review §4: the silent-laundering path now has a tell.

        An inbox-compromise attacker who REPLAYS the victim's device
        fingerprint used to get their location absorbed with zero
        user-visible signal (the alert was gated on new_device only).
        A new COUNTRY absorbed on a known fingerprint must notify.
        """
        user = _seed_user(db, "replayed@mail.com")
        # Both fingerprint forms are known: the victim's own browser
        # (client-supplied + derived) passed an earlier step-up. Only the
        # COUNTRY is new here — the pure fingerprint-replay-with-a-
        # different-browser shape is covered by the #83 corroboration
        # tests (test_edge_authentication.py).
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop", TESTCLIENT_DERIVED_FP),
            countries=("KE",),
            hours=_all_hours(),
        )

        with (
            patch(
                "app.modules.auth.service.AuthService._send_new_country_alert"
            ) as country_alert,
            patch(
                "app.modules.auth.service.AuthService._send_new_device_alert"
            ) as device_alert,
        ):
            _login_session(
                client, "replayed@mail.com", headers={**KNOWN_DEVICE, **US_GEO}
            )
        country_alert.assert_called_once()
        assert country_alert.call_args.kwargs.get("country") == "US"
        device_alert.assert_not_called()

        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "baseline",
                AuditEvent.action == "baseline_absorbed",
            )
            .all()
        )
        assert events
        assert events[-1].after["new_device"] is False
        assert events[-1].after["new_country"] is True

        # Re-login from the now-absorbed country: no further alert.
        with patch(
            "app.modules.auth.service.AuthService._send_new_country_alert"
        ) as country_alert:
            _login_session(
                client, "replayed@mail.com", headers={**KNOWN_DEVICE, **US_GEO}
            )
        country_alert.assert_not_called()

    def test_remember_touches_existing_entries_lru(self, db):
        """H13: the cap evicts the least-recently-VERIFIED entry."""
        now = ABAC_EVALUATION_TIME
        values = ["a", "b", "c"]  # legacy (unstamped) entries
        assert baselines._remember(values, "a", cap=3, now=now) is False
        assert baselines.entry_values(values) == ["b", "c", "a"], (
            "re-verified entry moves to most-recent"
        )
        # The touch re-stamped the entry (#67 line review §4).
        assert values[-1] == {"value": "a", "verified_at": now.isoformat()}
        # A new entry now evicts "b" (least recently verified), not "a".
        assert baselines._remember(values, "d", cap=3, now=now) is True
        assert baselines.entry_values(values) == ["c", "a", "d"]

    def test_aged_out_entry_no_longer_counts_and_reabsorbs_as_new(self, db):
        """#67 line review §4: absorbed trust ages out after the TTL.

        An entry past RISK_BASELINE_TRUST_TTL_DAYS stops counting as
        known — and re-absorbing its value reads as NEW trust (returns
        True → the owner is notified again), with a fresh stamp.
        """
        from datetime import timedelta

        now = ABAC_EVALUATION_TIME
        stale = now - timedelta(days=settings.RISK_BASELINE_TRUST_TTL_DAYS + 1)
        live = now - timedelta(days=1)
        entries = [
            {"value": "old-device", "verified_at": stale.isoformat()},
            {"value": "current-device", "verified_at": live.isoformat()},
        ]

        assert baselines.fresh_values(entries, now) == {"current-device"}
        # Legacy unstamped entries stay fresh (graceful pre-migration).
        assert baselines.fresh_values(["legacy"], now) == {"legacy"}

        # Re-absorbing the aged-out value is a NEW trust event.
        assert baselines._remember(entries, "old-device", cap=8, now=now) is True
        assert entries[-1] == {"value": "old-device", "verified_at": now.isoformat()}
        assert baselines.fresh_values(entries, now) == {
            "current-device",
            "old-device",
        }

    def test_aged_out_device_fires_soft_signal_again(self, client, db, trusted_ctx):
        """An aged-out device deviates from the baseline again end to end."""
        from datetime import timedelta

        stale = ABAC_EVALUATION_TIME - timedelta(
            days=settings.RISK_BASELINE_TRUST_TTL_DAYS + 1
        )
        user = _seed_user(db, "returning@mail.com")
        aged_device = {
            "value": "client:known-laptop",
            "verified_at": stale.isoformat(),
        }
        _seed_baseline(
            db,
            user,
            devices=(aged_device,),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers", headers={**headers, **KNOWN_DEVICE, **KE_GEO}
        )
        assert resp.status_code == 200, "one soft signal still allows"
        db.refresh(session)
        assert session.risk_score == settings.RISK_SCORE_NEW_DEVICE
        assert _soft_events(db, "new_device"), (
            "an entry past the trust TTL must fire new_device again"
        )
        assert not _soft_events(db, "new_country"), "the fresh KE entry still counts"

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
        assert "client:burner-phone" in baselines.entry_values(baseline.known_devices)
        assert "US" in baselines.entry_values(baseline.known_countries)


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
        now = ABAC_EVALUATION_TIME
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

    def test_soft_floor_plus_impossible_travel_challenges_at_defaults(
        self, client, db, trusted_ctx
    ):
        """#67 line review §3: THE most likely real-world FP lockout shape.

        At DEFAULT thresholds: a session that began on a new device in a
        new country carries a non-decaying floor of 50 (H10); ONE
        impossible-travel firing later (+70 → 120 ≥ terminate 100) must
        NOT terminate under the default mobile-heavy profile
        (RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION="challenge") — the "hard"
        evidence in that composition is carrier-CGNAT geolocation, this
        market's weakest signal. The user steps up; nothing is killed.
        """
        assert settings.RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION == "challenge", (
            "this regression pins the DEFAULT policy"
        )
        assert settings.RISK_TERMINATE_THRESHOLD == 100
        assert settings.RISK_CHALLENGE_THRESHOLD == 60

        user = _seed_user(db, "relocated@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        # Session start: new device + new country (US) = 50 — allowed,
        # logged, floored.
        first = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **US_GEO}
        )
        assert first.status_code == 200
        db.refresh(session)
        assert session.risk_floor == (
            settings.RISK_SCORE_NEW_DEVICE + settings.RISK_SCORE_NEW_COUNTRY
        )

        # A carrier exit-node hop: the geolocation jumps continents within
        # the same evaluation instant → impossible travel fires (+70).
        # 50 + 70 = 120 crosses terminate — and must clamp to a challenge.
        second = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **KE_GEO}
        )
        assert second.status_code == 401
        assert second.json()["error_code"] == "STEP_UP_REQUIRED", (
            "impossible travel under the default profile must cap at a "
            "step-up, never terminate"
        )
        db.refresh(session)
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value
        assert session.status != SessionStatus.TERMINATED.value
        assert session.risk_score >= settings.RISK_TERMINATE_THRESHOLD

        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "impossible_travel",
            )
            .all()
        )
        assert events, "the firing itself is still scored and audited"
        assert not (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "session_terminated",
            )
            .all()
        )

    def test_travel_terminate_policy_is_available_behind_config(
        self, client, db, trusted_ctx, monkeypatch
    ):
        """The previous behaviour stays available: with
        RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION="terminate" the same composition
        (floor 50 + travel 70 = 120 ≥ 100, hard evidence in the crossing
        batch) terminates the session."""
        monkeypatch.setattr(settings, "RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION", "terminate")

        user = _seed_user(db, "strict@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        first = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **US_GEO}
        )
        assert first.status_code == 200

        second = client.get(
            "/api/v1/customers", headers={**headers, **NEW_DEVICE, **KE_GEO}
        )
        assert second.status_code == 401
        db.refresh(session)
        assert session.status == SessionStatus.TERMINATED.value

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
            devices=("client:known-laptop", TESTCLIENT_DERIVED_FP),
            countries=("KE",),
            hours=_all_hours(),
        )

        session, headers = _craft_session(db, user)
        resp = client.get("/api/v1/customers", headers={**headers, **KNOWN_DEVICE})
        assert resp.status_code == 200
        assert session.risk_score == 0
        assert not _soft_events(db, "new_country")


class TestSessionStartSoftsDoNotDecay:
    """#67 review H10: decay-paced evasion of the session-start signals.

    Decay exists to forgive TRANSIENT noise. Session-start deviations are
    not transient — a session that began on an unknown device in an
    unknown country does not stop having begun there — so they anchor a
    non-decaying floor for the session's lifetime. Without it, an
    attacker paces anomalies so each decays before the next lands and
    accumulates below the challenge threshold forever.
    """

    def test_paced_anomaly_after_decay_window_still_challenges(
        self, client, db, trusted_ctx, monkeypatch
    ):
        from datetime import timedelta

        from app.common.authz.risk import effective_score

        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 2)
        user = _seed_user(db, "pacer@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop",),
            countries=("KE",),
            hours=_all_hours(),
        )
        session, headers = _craft_session(db, user)
        hijack = {**headers, **NEW_DEVICE, **US_GEO}

        # Session start from a new device + new country: 25+25 = 50 —
        # allowed and logged (below the 60 challenge threshold).
        assert client.get("/api/v1/customers", headers=hijack).status_code == 200
        db.refresh(session)
        assert session.risk_floor == (
            settings.RISK_SCORE_NEW_DEVICE + settings.RISK_SCORE_NEW_COUNTRY
        )

        # The attacker idles 6 hours: pure decay would shed all 50 points.
        # (Anchored on the pinned evaluation instant the gate reads.)
        session.risk_updated_at = ABAC_EVALUATION_TIME - timedelta(hours=6)
        db.commit()
        assert effective_score(session, ABAC_EVALUATION_TIME) == session.risk_floor, (
            "session-start soft evidence must not decay within the session"
        )

        # The next anomaly (mild volume, +30) lands on 50, not on 0:
        # 80 >= 60 challenges despite the pacing.
        statuses = [
            client.get("/api/v1/customers", headers=hijack).status_code
            for _ in range(4)
        ]
        assert 401 in statuses
        db.refresh(session)
        assert session.status == SessionStatus.CHALLENGE_REQUIRED.value


class TestUnusualHour:
    def test_unusual_hour_alone_only_logs(self, client, db, trusted_ctx):
        user = _seed_user(db, "latework@mail.com")
        _seed_baseline(
            db,
            user,
            devices=("client:known-laptop", TESTCLIENT_DERIVED_FP),
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
            devices=("client:known-laptop", TESTCLIENT_DERIVED_FP),
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
            db,
            user,
            devices=("client:known-laptop", TESTCLIENT_DERIVED_FP),
            countries=("KE",),
            hours=hours,
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
                "window_started_at": ABAC_EVALUATION_TIME.isoformat(),
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
            baselines._remember(
                baseline.known_devices, f"client:dev-{i}", 8, ABAC_EVALUATION_TIME
            )
        assert len(baseline.known_devices) == 8
        devices = baselines.entry_values(baseline.known_devices)
        assert devices[-1] == "client:dev-19"
        assert "client:dev-0" not in devices

    def test_hour_histogram_halves_at_cap(self, db):
        user = _seed_user(db, "unit4@mail.com")
        baseline = _seed_baseline(db, user, hours={"9": 4998, "23": 1})
        baselines._bump_hour(baseline, 9)  # reaches the 5000 cap
        assert baseline.hour_counts["9"] == 2499
        assert "23" not in baseline.hour_counts  # 1 // 2 == 0: pruned
        assert baseline.hour_observations == 2499

    def test_learning_sample_cadence_is_weight_compensated(self, db, monkeypatch):
        """#67 line review §5: 1-in-N learning, unbiased by weighting.

        With RISK_BASELINE_LEARN_SAMPLE_N=4, only every 4th request per
        user touches the baseline row — and each applied observation
        counts for 4, so hour histograms and per-window volume counts
        keep the same expectation as per-request learning.
        """
        import uuid as uuid_mod
        from datetime import UTC as UTC_TZ
        from datetime import datetime as dt

        from app.common.authz import risk
        from app.common.authz.context import AccessContext
        from app.common.authz.sensitivity import classify_path

        monkeypatch.setattr(settings, "RISK_BASELINE_LEARN_SAMPLE_N", 4)
        risk._learn_sample_counters.clear()
        user = _seed_user(db, "sampled@mail.com")

        def _context() -> AccessContext:
            path = "/api/v1/customers"
            return AccessContext(
                principal="user",
                user_id=user.id,
                session_id=uuid_mod.uuid4(),
                ip="203.0.113.10",
                ip_denylisted=False,
                geo=None,
                device_fingerprint="derived:abc123",
                requested_at=dt(2026, 8, 14, 9, 0, tzinfo=UTC_TZ),
                local_hour=9,
                method="GET",
                path=path,
                sensitivity=classify_path(path),
            )

        for _ in range(3):
            risk._learn_sampled(db, user.id, _context())
        assert db.get(UserBaseline, user.id) is None, (
            "requests 1-3 must not touch the baseline row"
        )

        risk._learn_sampled(db, user.id, _context())  # the 4th learns
        db.flush()
        baseline = db.get(UserBaseline, user.id)
        assert baseline is not None
        assert baseline.hour_counts.get("9") == 4, "weight-compensated"
        assert baseline.hour_observations == 4
        entry = baseline.volume_baselines[classify_path("/api/v1/customers").value]
        assert entry["count"] == 4

        # N=1 keeps the historical per-request behaviour.
        monkeypatch.setattr(settings, "RISK_BASELINE_LEARN_SAMPLE_N", 1)
        risk._learn_sampled(db, user.id, _context())
        db.flush()
        assert baseline.hour_counts.get("9") == 5

    def test_concurrent_first_insert_is_idempotent(self, db, monkeypatch):
        """#67 review H14 (prior L3): the first-INSERT race must not 500.

        Two concurrent first-scored requests both observe "no baseline"
        and both INSERT; the loser's IntegrityError must roll back only
        the attempted insert (savepoint) and re-fetch the winner's row.
        The lost race is simulated by blinding the initial lookup while a
        'concurrent' row already exists.

        The savepoint is the point: a full-session rollback would expire
        every object the caller's request had already loaded (the gate's
        session row, the current user), turning a harmless lost race into
        DetachedInstanceError/expiry surprises mid-request. So the loser
        path must leave pre-loaded objects live and the transaction usable.
        """
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy.orm import Session as SaSession

        user = _seed_user(db, "racer@mail.com")
        # The concurrent request already inserted (and committed) the row.
        concurrent_row = _seed_baseline(db, user)
        # Forget the ROW locally so the recovery db.get truly re-queries;
        # `user` stays attached — it plays the caller's pre-loaded object.
        db.expunge(concurrent_row)
        user_id = user.id
        user_email = user.email  # load now: commit expired the attributes

        calls = {"n": 0}
        original_get = SaSession.get

        def blinded_first_get(self, entity, ident, **kw):
            if getattr(entity, "__name__", "") == "UserBaseline":
                calls["n"] += 1
                if calls["n"] == 1:
                    return None  # the race window: row invisible to us
            return original_get(self, entity, ident, **kw)

        monkeypatch.setattr(SaSession, "get", blinded_first_get)
        baseline = baselines.get_or_create_baseline(db, user_id)
        assert baseline is not None
        assert baseline.user_id == user_id
        # Pre-loaded objects survive the lost race: still attached, not
        # expired by any full-session rollback (the H14 defect shape).
        state = sa_inspect(user)
        assert state.session is db
        assert not state.expired
        assert user.email == user_email
        # And the surrounding transaction is still usable (no 500 shape).
        db.flush()
