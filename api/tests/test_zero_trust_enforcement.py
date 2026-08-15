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
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.common.audit import AuditEvent
from app.common.authz import db_guard
from app.common.authz.engine import ALLOW_VERDICT, Decision, PolicyVerdict
from app.common.exceptions import ForbiddenException
from app.common.security import create_access_token, hash_password
from app.constants.enums import UserRole
from app.lib.config import settings
from app.modules.auth.models import User
from tests.conftest import TestingSessionLocal

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


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}


def _freeze_local_hour(monkeypatch, hour: int) -> None:
    """Freeze the gate's clock so the tenant-local hour is ``hour``."""
    tz = ZoneInfo(settings.REPORTING_TIMEZONE)
    local = datetime(2026, 8, 14, hour, 30, tzinfo=tz)
    frozen = local.astimezone(UTC)
    monkeypatch.setattr("app.common.authz.context._now", lambda: frozen)


@pytest.fixture
def off_hours_window(monkeypatch):
    """Enable the 22h → 6h off-hours window (conftest disables it suite-wide)."""
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

        _freeze_local_hour(monkeypatch, 23)
        night = client.get("/api/v1/platform/owners", headers=_auth(operator))
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
        night = client.post("/api/v1/invoices", json={}, headers=_auth(admin))
        assert night.status_code == 401
        assert night.json()["error_code"] == "STEP_UP_REQUIRED"

    def test_confidential_read_stays_allowed_at_night(
        self, client, db, off_hours_window, monkeypatch
    ):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        _freeze_local_hour(monkeypatch, 23)
        resp = client.get("/api/v1/invoices", headers=_auth(admin))
        assert resp.status_code == 200

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


class TestDecisionAuditTrail:
    def test_allow_decisions_are_audited_with_actor(self, client, db):
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
