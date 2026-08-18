"""End-to-end tests for zero-trust enforcement (issue #67).

Drives the real app through the gate wired in ``app/main.py`` and pins:

- the authz matrix over HTTP: the same user, role and permission gets a
  different outcome when only the context (time of day, IP reputation,
  geolocation) changes;
- step-up challenges surface as 401 ``STEP_UP_REQUIRED`` with the ``otp``
  challenge hint (the client re-runs the existing login → OTP flow);
- every decision is audited on the append-only trail — and non-allow
  decisions survive the rollback their own rejection triggers;
- probes (PUBLIC) are never evaluated, denied, or audited;
- ABAC layers ON TOP of RBAC: it never weakens the ADR-0011 platform /
  tenant isolation, and authentication still owns the 401 for missing
  credentials;
- the DB-layer guard refuses ORM access on request-scoped sessions that
  carry no ALLOW verdict (no implicit trust from a prior successful auth).
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.common.audit import AuditEvent
from app.common.authz import db_guard
from app.common.authz.engine import ALLOW_VERDICT, Decision, PolicyVerdict
from app.common.exceptions import ForbiddenException
from app.common.security import hash_password
from app.constants.enums import UserRole
from app.lib.config import settings
from app.modules.auth.models import User
from tests.conftest import TestingSessionLocal, auth_headers

_NIL = uuid.UUID(int=0)


def _seed_user(db, email: str, role: UserRole) -> User:
    user = User(
        email=email,
        password_hash=hash_password("Sup3r!Secret"),
        first_name="Test",
        last_name="User",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(user: User, *, stepped_up: bool = True, stepped_up_at=None) -> dict:
    """Bearer headers for ``user`` (stepped-up like a real login by default)."""
    return auth_headers(user, stepped_up=stepped_up, stepped_up_at=stepped_up_at)


def _frozen_instant(hour: int) -> datetime:
    """The UTC instant whose tenant-local hour is ``hour``."""
    tz = ZoneInfo(settings.REPORTING_TIMEZONE)
    return datetime(2026, 8, 14, hour, 30, tzinfo=tz).astimezone(UTC)


def _freeze_local_hour(monkeypatch, hour: int) -> datetime:
    """Freeze the gate's clock so the tenant-local hour is ``hour``.

    Returns the frozen instant so a test can mint a `sua` claim consistent
    with it — a token stamped at real wall-clock time is not meaningfully
    fresh or stale relative to a frozen request.
    """
    frozen = _frozen_instant(hour)
    monkeypatch.setattr("app.common.authz.context._now", lambda: frozen)
    return frozen


@pytest.fixture
def off_hours_window(monkeypatch):
    """Pin the 22h → 6h off-hours window explicitly (conftest keeps it live)."""
    monkeypatch.setattr(settings, "ABAC_OFF_HOURS_START", 22)
    monkeypatch.setattr(settings, "ABAC_OFF_HOURS_END", 6)


def _decision_rows(db, action: str) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "access_decision",
            AuditEvent.action == action,
        )
        .all()
    )


class TestSameRoleDifferentContext:
    """The issue's core acceptance: identical RBAC, different outcomes."""

    def test_restricted_read_day_allowed_night_challenged(
        self, client, db, off_hours_window, monkeypatch
    ):
        operator = _seed_user(db, "op@mail.com", UserRole.PLATFORM_OPERATOR)

        _freeze_local_hour(monkeypatch, 11)
        day = client.get("/api/v1/platform/owners", headers=_auth(operator))
        assert day.status_code == 200

        # Night, on a session whose last OTP is long past: challenged.
        _freeze_local_hour(monkeypatch, 23)
        night = client.get(
            "/api/v1/platform/owners", headers=_auth(operator, stepped_up=False)
        )
        assert night.status_code == 401
        body = night.json()
        assert body["error_code"] == "STEP_UP_REQUIRED"
        assert body["details"]["challenge"] == "otp"

    def test_confidential_write_day_vs_night(
        self, client, db, off_hours_window, monkeypatch
    ):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)

        # Daytime: the gate allows, so the request reaches body validation
        # (422 for the empty payload) — same role, same permission.
        _freeze_local_hour(monkeypatch, 11)
        day = client.post("/api/v1/invoices", json={}, headers=_auth(admin))
        assert day.status_code == 422

        # Night: the gate challenges BEFORE validation ever runs.
        _freeze_local_hour(monkeypatch, 23)
        night = client.post(
            "/api/v1/invoices", json={}, headers=_auth(admin, stepped_up=False)
        )
        assert night.status_code == 401
        assert night.json()["error_code"] == "STEP_UP_REQUIRED"

    def test_confidential_read_stays_allowed_at_night(
        self, client, db, off_hours_window, monkeypatch
    ):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        _freeze_local_hour(monkeypatch, 23)
        resp = client.get("/api/v1/invoices", headers=_auth(admin, stepped_up=False))
        assert resp.status_code == 200

    def test_nightly_internal_scheduler_call_is_never_challenged(
        self, client, db, off_hours_window, monkeypatch
    ):
        """The nightly cron (02:00, inside the window) must keep working.

        A machine caller has no inbox: an OTP challenge for it is a
        permanent lockout, not a step-up. Its trust model stays
        verify_internal_secret's constant-time comparison — which still
        rejects a wrong secret below.
        """
        monkeypatch.setattr(settings, "INTERNAL_API_SECRET", "night-secret")
        _freeze_local_hour(monkeypatch, 2)

        ok = client.post(
            "/api/v1/invoices/internal/transition-overdue",
            headers={"X-Internal-Secret": "night-secret"},
        )
        assert ok.status_code == 200

        # The exemption never weakens the secret check itself — and (H9) a
        # WRONG secret never earns the service classification in the first
        # place: the caller stays anonymous, so the gate's off-hours rule
        # challenges it before verify_internal_secret would even run.
        # Either way: 401, and the nightly job only works with the real
        # secret.
        bad = client.post(
            "/api/v1/invoices/internal/transition-overdue",
            headers={"X-Internal-Secret": "wrong"},
        )
        assert bad.status_code == 401

    def test_garbage_internal_secret_cannot_dodge_challenges(
        self, client, db, off_hours_window, monkeypatch
    ):
        """#67 review H9: service classification requires a VERIFIED secret.

        If header PRESENCE alone classified the caller as "service", anyone
        could attach a garbage X-Internal-Secret to inherit the machine
        exemption from the off-hours / unknown-geo challenge rules. The
        principal is granted only when the header value matches
        INTERNAL_API_SECRET in constant time.

        A wrong secret classifies the caller as ANONYMOUS — and an
        anonymous caller is never step-up-challenged (it has no account to
        step up); it is refused by verify_internal_secret itself, which
        owns authentication for this surface. The refusal must not be a
        STEP_UP_REQUIRED: that would invite an unanswerable OTP round trip.
        """
        monkeypatch.setattr(settings, "INTERNAL_API_SECRET", "real-secret-value")
        _freeze_local_hour(monkeypatch, 2)

        spoof = client.post(
            "/api/v1/invoices/internal/transition-overdue",
            headers={"X-Internal-Secret": "garbage"},
        )
        assert spoof.status_code == 401
        assert spoof.json()["error_code"] != "STEP_UP_REQUIRED"

        # With no secret configured at all, presence earns nothing either:
        # the endpoint fails closed (403 — internal surface not enabled).
        monkeypatch.setattr(settings, "INTERNAL_API_SECRET", "")
        unconfigured = client.post(
            "/api/v1/invoices/internal/transition-overdue",
            headers={"X-Internal-Secret": "anything"},
        )
        assert unconfigured.status_code == 403

    def test_denylisted_ip_denied_even_with_valid_admin_token(
        self, client, db, monkeypatch
    ):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        ok = client.get("/api/v1/customers", headers=_auth(admin))
        assert ok.status_code == 200

        # Same user, same token, same endpoint — the source turns bad.
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "testclient")
        denied = client.get("/api/v1/customers", headers=_auth(admin))
        assert denied.status_code == 403
        assert denied.json()["details"]["required_permission"] == "abac:ip_reputation"

    def test_geo_blocklist_denies_via_trusted_edge_header(
        self, client, db, monkeypatch
    ):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        monkeypatch.setattr(settings, "ABAC_GEO_BLOCKLIST", "KP")

        allowed = client.get(
            "/api/v1/customers",
            headers={**_auth(admin), "X-Geo-Country": "KE"},
        )
        assert allowed.status_code == 200

        denied = client.get(
            "/api/v1/customers",
            headers={**_auth(admin), "X-Geo-Country": "KP"},
        )
        assert denied.status_code == 403
        assert denied.json()["details"]["required_permission"] == "abac:geo_blocklist"

    def test_untrusted_geo_headers_are_ignored(self, client, db, monkeypatch):
        """Without the trust switch, a spoofed geo header changes nothing."""
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        monkeypatch.setattr(settings, "ABAC_GEO_BLOCKLIST", "KP")
        resp = client.get(
            "/api/v1/customers",
            headers={**_auth(admin), "X-Geo-Country": "KP"},
        )
        assert resp.status_code == 200


class TestChallengesAreSatisfiable:
    """A CHALLENGE must be answerable, or it is a lockout wearing a badge.

    The off-hours rule reads the wall clock and the path — inputs a new
    token pair cannot change. Without the `sua` step-up claim its 401
    repeats for the whole window however many times the user authenticates,
    and the only reason CI stayed green was that the suite disabled the
    window outright rather than fixing it.
    """

    def test_fresh_step_up_clears_the_off_hours_challenge(
        self, client, db, off_hours_window, monkeypatch
    ):
        operator = _seed_user(db, "op@mail.com", UserRole.PLATFORM_OPERATOR)
        frozen = _freeze_local_hour(monkeypatch, 23)

        blocked = client.get(
            "/api/v1/platform/owners", headers=_auth(operator, stepped_up=False)
        )
        assert blocked.status_code == 401
        assert blocked.json()["error_code"] == "STEP_UP_REQUIRED"

        # Completing login → OTP stamps `sua`. THE SAME request now passes:
        # same hour, same user, same endpoint.
        cleared = client.get(
            "/api/v1/platform/owners",
            headers=_auth(operator, stepped_up_at=frozen),
        )
        assert cleared.status_code == 200

    def test_step_up_expires_and_challenges_again(
        self, client, db, off_hours_window, monkeypatch
    ):
        """Clearance is a lease, not a key: it lapses and the guard returns."""
        operator = _seed_user(db, "op@mail.com", UserRole.PLATFORM_OPERATOR)
        frozen = _freeze_local_hour(monkeypatch, 23)
        stale = frozen - timedelta(minutes=settings.ABAC_STEP_UP_TTL_MINUTES + 1)

        resp = client.get(
            "/api/v1/platform/owners", headers=_auth(operator, stepped_up_at=stale)
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "STEP_UP_REQUIRED"

    def test_future_dated_step_up_is_not_honoured(
        self, client, db, off_hours_window, monkeypatch
    ):
        """Clock skew or a forged-but-signed claim earns nothing (fail closed)."""
        operator = _seed_user(db, "op@mail.com", UserRole.PLATFORM_OPERATOR)
        frozen = _freeze_local_hour(monkeypatch, 23)

        resp = client.get(
            "/api/v1/platform/owners",
            headers=_auth(operator, stepped_up_at=frozen + timedelta(hours=6)),
        )
        assert resp.status_code == 401

    def test_step_up_does_not_widen_access(
        self, client, db, off_hours_window, monkeypatch
    ):
        """`sua` answers the CONTEXT objection only — RBAC is untouched.

        A tenant admin holding a perfectly fresh OTP still cannot reach the
        platform surface: ABAC only ever restricts, so satisfying one of its
        rules must never hand out what RBAC withholds (ADR-0011).
        """
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        frozen = _freeze_local_hour(monkeypatch, 23)

        resp = client.get(
            "/api/v1/platform/owners", headers=_auth(admin, stepped_up_at=frozen)
        )
        assert resp.status_code == 403

    def test_step_up_does_not_clear_a_denylisted_ip(
        self, client, db, off_hours_window, monkeypatch
    ):
        """A DENY is not a challenge: no amount of OTP makes a bad IP good."""
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        frozen = _freeze_local_hour(monkeypatch, 23)
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "testclient")

        resp = client.get(
            "/api/v1/customers", headers=_auth(admin, stepped_up_at=frozen)
        )
        assert resp.status_code == 403


class TestNightClockRegression:
    """Pipeline 2767217667: the suite went red ONLY when CI ran at night.

    Two business tests installed their HTTP actor via
    ``app.dependency_overrides[get_current_user]`` — a pattern that sends
    no bearer token, so the gate classified the caller as "anonymous" and
    the off-hours rule challenged it whenever the job happened to run
    inside the 22h → 6h tenant-local window. Two defects, both pinned here
    under an explicitly frozen NIGHT clock:

    1. Semantics: an unauthenticated request can never satisfy an OTP
       step-up — challenging "anonymous" was a lockout, not a step-up.
       Authentication owns the 401 for missing credentials (ADR-0012);
       the CHALLENGE rules apply only to authenticated ``user`` principals.
    2. Determinism: the gate's evaluation clock is pinned suite-wide
       (conftest ``pinned_abac_clock``), so no test's outcome depends on
       the wall-clock hour CI happens to run at.
    """

    def test_night_stale_user_challenged_fresh_step_up_allowed(
        self, client, db, off_hours_window, monkeypatch
    ):
        """At night, the SAME endpoint challenges a stale session and
        clears one holding a fresh step-up (an ALLOW verdict)."""
        operator = _seed_user(db, "op@mail.com", UserRole.PLATFORM_OPERATOR)
        frozen = _freeze_local_hour(monkeypatch, 23)

        challenged = client.get(
            "/api/v1/platform/owners", headers=_auth(operator, stepped_up=False)
        )
        assert challenged.status_code == 401
        assert challenged.json()["error_code"] == "STEP_UP_REQUIRED"

        allowed = client.get(
            "/api/v1/platform/owners", headers=_auth(operator, stepped_up_at=frozen)
        )
        assert allowed.status_code == 200

    def test_night_anonymous_gets_auth_401_not_a_step_up(
        self, client, db, off_hours_window, monkeypatch
    ):
        """No credentials at night: refused by AUTHENTICATION, not ABAC.

        A step-up challenge asks the caller to prove control of an inbox —
        an anonymous request identifies no account, so the only coherent
        answer is the ordinary missing-credentials 401. It must also be the
        SAME answer day and night, or the response contract flips with the
        clock.
        """
        _freeze_local_hour(monkeypatch, 23)

        night = client.get("/api/v1/owner")  # RESTRICTED, no credentials
        assert night.status_code == 401
        assert night.json()["error_code"] != "STEP_UP_REQUIRED"
        assert not _decision_rows(db, "policy_challenge"), (
            "no challenge decision may be recorded for an anonymous caller"
        )

        _freeze_local_hour(monkeypatch, 11)
        day = client.get("/api/v1/owner")
        assert (day.status_code, day.json()["error_code"]) == (
            night.status_code,
            night.json()["error_code"],
        ), "the anonymous refusal must not depend on the wall clock"

    def test_night_dependency_override_actor_reaches_the_endpoint(
        self, client, db, off_hours_window, monkeypatch
    ):
        """The exact shape that failed in CI: an overridden actor, at night.

        ``dependency_overrides[get_current_user]`` installs the identity
        WITHOUT a bearer token, so the gate sees "anonymous". The override
        resolves the user after the gate allows, exactly like the two
        tests that failed (test_sales_pricing_settings_read,
        test_owner_endpoint_exposes_resolved_template_and_validates).
        """
        from types import SimpleNamespace

        from app.common.dependencies import get_current_user
        from app.main import app

        identity = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN)
        app.dependency_overrides[get_current_user] = lambda: identity
        try:
            _freeze_local_hour(monkeypatch, 23)
            night = client.get("/api/v1/owner/settings/sales-pricing")
            assert night.status_code == 200

            _freeze_local_hour(monkeypatch, 11)
            day = client.get("/api/v1/owner/settings/sales-pricing")
            assert day.status_code == 200
        finally:
            app.dependency_overrides.pop(get_current_user, None)


class TestDecisionAuditTrail:
    def test_allow_decisions_are_audited_with_actor(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_AUDIT_ALLOW_DECISIONS", True)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        resp = client.get("/api/v1/customers", headers=_auth(admin))
        assert resp.status_code == 200

        rows = _decision_rows(db, "policy_allow")
        mine = [r for r in rows if r.actor_id == admin.id]
        assert mine, "expected an audited ALLOW decision for the actor"
        payload = mine[-1].after
        assert payload["path"] == "/api/v1/customers"
        assert payload["sensitivity"] == "internal"
        assert payload["method"] == "GET"
        assert payload["principal"] == "user"

    def test_allow_decisions_are_not_audited_by_default(self, client, db):
        """The per-request ALLOW row is opt-in.

        Auditing every allow adds an INSERT to every business request for
        rows with little evidentiary value. Non-allow decisions are always
        recorded regardless (the two tests below).
        """
        assert settings.ABAC_AUDIT_ALLOW_DECISIONS is False
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        before = len(_decision_rows(db, "policy_allow"))

        assert client.get("/api/v1/customers", headers=_auth(admin)).status_code == 200

        assert len(_decision_rows(db, "policy_allow")) == before

    def test_challenge_decision_survives_its_own_rejection(
        self, client, db, off_hours_window, monkeypatch
    ):
        operator = _seed_user(db, "op@mail.com", UserRole.PLATFORM_OPERATOR)
        _freeze_local_hour(monkeypatch, 23)
        resp = client.get("/api/v1/platform/owners", headers=_auth(operator))
        assert resp.status_code == 401

        rows = _decision_rows(db, "policy_challenge")
        assert any(
            r.actor_id == operator.id and r.after["rule"] == "off_hours" for r in rows
        )

    def test_deny_decision_survives_its_own_rejection(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "testclient")
        resp = client.get("/api/v1/customers")
        assert resp.status_code == 403

        rows = _decision_rows(db, "policy_deny")
        assert rows, "the deny decision must be durably audited"
        assert rows[-1].after["rule"] == "ip_reputation"
        # Anonymous request: no actor, nil entity id.
        assert rows[-1].actor_id is None
        assert rows[-1].entity_id == _NIL

    def test_public_probes_are_never_evaluated_or_audited(
        self, client, db, monkeypatch
    ):
        # Even a denylisted source can hit the load-balancer probe.
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "testclient")
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200

        assert not _decision_rows(db, "policy_allow")
        assert not _decision_rows(db, "policy_deny")


class TestLayeringOnRbac:
    """ABAC restricts; it never widens what RBAC / authn would refuse."""

    def test_platform_operator_still_isolated_from_tenant_gates(self, client, db):
        """ADR-0011: the operator role never satisfies a tenant role gate."""
        operator = _seed_user(db, "op@mail.com", UserRole.PLATFORM_OPERATOR)
        resp = client.get("/api/v1/owner/modules", headers=_auth(operator))
        assert resp.status_code == 403

    def test_tenant_admin_still_rejected_from_platform_surface(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        resp = client.get("/api/v1/platform/owners", headers=_auth(admin))
        assert resp.status_code == 403

    def test_missing_credentials_still_401(self, client, db):
        """The gate's contextual ALLOW is not authentication."""
        resp = client.get("/api/v1/customers")
        assert resp.status_code == 401

    def test_member_role_gates_unchanged(self, client, db):
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)
        # Members read customers fine; ABAC added no new obstacle.
        resp = client.get("/api/v1/customers", headers=_auth(member))
        assert resp.status_code == 200


class TestDbGuard:
    """No implicit trust at the database layer."""

    def _request_scoped_session(self):
        session = TestingSessionLocal()
        db_guard.mark_request_scoped(session)
        return session

    def test_request_scoped_session_without_verdict_is_refused(self, db):
        session = self._request_scoped_session()
        try:
            with pytest.raises(ForbiddenException):
                session.query(User).all()
        finally:
            session.close()

    def test_uncleared_flush_is_refused(self, db):
        session = self._request_scoped_session()
        try:
            session.add(
                User(
                    email="ghost@mail.com",
                    password_hash="x",
                    first_name="G",
                    last_name="H",
                )
            )
            with pytest.raises(ForbiddenException):
                session.flush()
        finally:
            session.rollback()
            session.close()

    def test_allow_verdict_clears_the_session(self, db):
        session = self._request_scoped_session()
        try:
            db_guard.set_verdict(session, ALLOW_VERDICT)
            assert session.query(User).all() == []
        finally:
            session.close()

    def test_non_allow_verdict_keeps_session_fenced(self, db):
        session = self._request_scoped_session()
        try:
            db_guard.set_verdict(
                session,
                PolicyVerdict(
                    decision=Decision.DENY, rule="ip_reputation", reason="test"
                ),
            )
            with pytest.raises(ForbiddenException):
                session.query(User).all()
        finally:
            session.close()

    def test_audit_bypass_is_scoped(self, db):
        session = self._request_scoped_session()
        try:
            with db_guard.audit_bypass(session):
                assert session.query(User).all() == []
            with pytest.raises(ForbiddenException):
                session.query(User).all()
        finally:
            session.close()

    def test_non_request_sessions_untouched(self, db):
        # The plain fixture session carries no marker: scripts, schedulers
        # and direct test access keep working.
        assert db.query(User).count() == 0
