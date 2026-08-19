"""End-to-end legs of the missing-geo CONFIDENTIAL-read backstop (#84).

The pure rule matrix lives in test_abac_policy_engine.py
(``TestGeoBackstopConfidentialReads``); these tests drive real requests
through the zero-trust gate so the volume window, the sua step-up and
the audit trail are the production ones:

- with geo configured, a CONFIDENTIAL bulk read with stripped geo
  headers steps up — and a single read does not (issue acceptance);
- geo-carrying requests are untouched;
- the challenge is answerable: a fresh ``sua`` step-up claim clears it;
- the backstop never terminates — the CGNAT-friendly posture is intact.
"""

import pytest

from app.common.audit import AuditEvent
from app.common.security import create_access_token, hash_password
from app.constants.enums import SessionStatus, UserRole
from app.lib.config import settings
from app.modules.auth.models import User, UserSession
from tests.conftest import ABAC_EVALUATION_TIME

VALID_PASSWORD = "Sup3r$ecret!"

KE_GEO = {"X-Geo-Country": "KE", "X-Geo-Lat": "-1.286", "X-Geo-Lon": "36.817"}


def _seed_user(db, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password(VALID_PASSWORD),
        first_name="Backstop",
        last_name="Tester",
        role=UserRole.MEMBER.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _stale_session(db, user: User) -> tuple[UserSession, dict]:
    """A live session whose token carries NO fresh step-up claim.

    Models the attack shape the backstop exists for: a stolen/stale
    token (no ``sua`` — fail closed reads as "not stepped up") pulling
    CONFIDENTIAL data with the geo headers omitted.
    """
    session = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value)
    db.add(session)
    db.commit()
    token = create_access_token(subject=str(user.id), extra={"sid": str(session.id)})
    return session, {"Authorization": f"Bearer {token}"}


def _stepped_up_session(db, user: User) -> dict:
    """A session whose token carries a FRESH ``sua`` claim.

    Stamped at the suite's pinned evaluation instant (the clock every
    request is evaluated on — conftest ``pinned_abac_clock``), exactly as
    the shared ``auth_headers`` helper does: the product's ``verify_otp``
    stamps the real wall clock, which the frozen timeline would read as
    future-dated (fail closed).
    """
    session = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value)
    db.add(session)
    db.commit()
    token = create_access_token(
        subject=str(user.id),
        extra={
            "sid": str(session.id),
            "sua": int(ABAC_EVALUATION_TIME.timestamp()),
        },
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def backstop_env(monkeypatch):
    """Geo signal configured; small window so the threshold is reachable.

    RISK_VOLUME_MAX_REQUESTS=4 at the default 50% puts the backstop
    threshold at 2 units: reads 1-2 pass, read 3 (window already at 2)
    challenges. The exfiltration ceiling sits at 4x5=20 — far out of
    reach, so any 401 observed here is the backstop, not a risk kill.
    """
    monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
    monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 4)


class TestGeoBackstopEndToEnd:
    def test_bulk_geo_less_confidential_reads_step_up(self, client, db, backstop_env):
        user = _seed_user(db, "bulk@mail.com")
        session, headers = _stale_session(db, user)

        first = client.get("/api/v1/invoices", headers=headers)
        assert first.status_code == 200, "a single geo-less read must pass"
        second = client.get("/api/v1/invoices", headers=headers)
        assert second.status_code == 200

        third = client.get("/api/v1/invoices", headers=headers)
        assert third.status_code == 401
        assert third.json()["error_code"] == "STEP_UP_REQUIRED"

        # Never a termination, and the session is not even flipped: the
        # static challenge is answerable via the ordinary step-up (the
        # CGNAT posture is untouched for geo-less traffic).
        db.refresh(session)
        assert session.status == SessionStatus.ACTIVE.value

        decisions = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "access_decision",
                AuditEvent.action == "policy_challenge",
            )
            .all()
        )
        assert decisions
        assert decisions[-1].after["rule"] == "unknown_geo_confidential_read"
        assert not (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "session_terminated",
            )
            .all()
        )

    def test_geo_carrying_reads_are_untouched(self, client, db, backstop_env):
        user = _seed_user(db, "located@mail.com")
        _, headers = _stale_session(db, user)

        for _ in range(3):
            resp = client.get("/api/v1/invoices", headers={**headers, **KE_GEO})
            assert resp.status_code == 200, (
                "geo-carrying reads must never trip the missing-geo backstop"
            )

    def test_unconfigured_geo_signal_changes_nothing(self, client, db, monkeypatch):
        """Acceptance: trust-headers off = no signal, no penalty."""
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", False)
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 4)
        user = _seed_user(db, "plain@mail.com")
        _, headers = _stale_session(db, user)

        for _ in range(3):
            assert client.get("/api/v1/invoices", headers=headers).status_code == 200

    def test_fresh_step_up_clears_the_backstop(self, client, db, backstop_env):
        """A fresh ``sua`` (a completed OTP round trip) answers it."""
        user = _seed_user(db, "answered@mail.com")
        headers = _stepped_up_session(db, user)

        for _ in range(4):
            resp = client.get("/api/v1/invoices", headers=headers)
            assert resp.status_code == 200, (
                "a stepped-up caller must not be challenged by the backstop"
            )

    def test_internal_reads_not_gated(self, client, db, backstop_env):
        """The backstop is scoped to CONFIDENTIAL+; INTERNAL stays free."""
        user = _seed_user(db, "internal@mail.com")
        _, headers = _stale_session(db, user)

        for _ in range(3):
            assert client.get("/api/v1/customers", headers=headers).status_code == 200

    def test_rejected_requests_keep_challenging_within_window(
        self, client, db, backstop_env
    ):
        """The static rejection does not charge the window, so the state
        is stable: every further geo-less read in the window challenges
        (no oscillation between 200 and 401)."""
        user = _seed_user(db, "steady@mail.com")
        _, headers = _stale_session(db, user)

        assert client.get("/api/v1/invoices", headers=headers).status_code == 200
        assert client.get("/api/v1/invoices", headers=headers).status_code == 200
        for _ in range(3):
            resp = client.get("/api/v1/invoices", headers=headers)
            assert resp.status_code == 401
            assert resp.json()["error_code"] == "STEP_UP_REQUIRED"
