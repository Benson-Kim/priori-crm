"""ABAC / session-risk ops surface (issue #85).

Covers the acceptance criteria end to end:

- an operator lists and terminates any user's sessions without SQL;
  termination is audited (operator as actor) and immediate (denylisted
  ``sid`` — the victim's live token dies on its next request);
- an operator inspects and expires individual absorbed devices /
  countries; expiry is audited, and the next use of that context
  re-fires the soft signal, with re-absorption + owner notification on
  a passed step-up;
- the weekly ``soft_clamp`` review works from ``GET /admin/risk-events``
  (ADR-0012's ops query section points at it);
- every endpoint is RESTRICTED-classified and covered by the ABAC
  matrix (day-vs-night leg below), with the RBAC denial legs pinned for
  every non-operator role and for unauthenticated callers.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

from app.common.audit import AuditEvent
from app.common.authz import baselines
from app.common.authz.engine import Decision, evaluate
from app.common.authz.sensitivity import SensitivityLevel, classify_path
from app.common.security import create_access_token, hash_password
from app.constants.enums import SessionStatus, UserRole
from app.lib.config import settings
from app.modules.auth.models import User, UserBaseline, UserSession
from tests.conftest import ABAC_EVALUATION_TIME, auth_headers

VALID_PASSWORD = "Sup3r$ecret!"

FRESH_STAMP = (ABAC_EVALUATION_TIME - timedelta(days=1)).isoformat()
STALE_STAMP = (
    ABAC_EVALUATION_TIME - timedelta(days=settings.RISK_BASELINE_TRUST_TTL_DAYS + 5)
).isoformat()


def _seed_user(db, email: str, role: UserRole = UserRole.MEMBER) -> User:
    user = User(
        email=email,
        password_hash=hash_password(VALID_PASSWORD),
        first_name="Ops",
        last_name="Tester",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _admin(db) -> tuple[User, dict]:
    admin = _seed_user(db, "opsadmin@mail.com", role=UserRole.ADMIN)
    return admin, auth_headers(admin)


def _victim_session(db, user: User, **fields) -> tuple[UserSession, dict]:
    session = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value, **fields)
    db.add(session)
    db.commit()
    token = create_access_token(
        subject=str(user.id),
        extra={"sid": str(session.id), "sua": int(ABAC_EVALUATION_TIME.timestamp())},
    )
    return session, {"Authorization": f"Bearer {token}"}


class TestAdminSurfaceClassification:
    """Every /admin path is RESTRICTED and gets the full ABAC treatment."""

    pytestmark = pytest.mark.no_db

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/admin",
            "/api/v1/admin/risk-events",
            "/api/v1/admin/users/9a1/sessions",
            "/api/v1/admin/sessions/9a1/terminate",
            "/api/v1/admin/users/9a1/baseline",
        ],
    )
    def test_admin_paths_classify_restricted(self, path):
        assert classify_path(path) is SensitivityLevel.RESTRICTED

    def test_prefix_similarity_does_not_leak(self):
        assert classify_path("/api/v1/adminx") is SensitivityLevel.INTERNAL

    def test_matrix_day_vs_night(self, monkeypatch):
        """Same operator, same permission — different context, different
        outcome: the off-hours rule challenges the RESTRICTED surface at
        night without a fresh step-up (the ABAC matrix leg issue #85's
        acceptance demands for the new endpoints)."""
        monkeypatch.setattr(settings, "ABAC_OFF_HOURS_START", 22)
        monkeypatch.setattr(settings, "ABAC_OFF_HOURS_END", 6)
        from datetime import UTC, datetime

        from app.common.authz.context import AccessContext

        def ctx(hour: int) -> AccessContext:
            path = "/api/v1/admin/risk-events"
            return AccessContext(
                principal="user",
                user_id=uuid.uuid4(),
                session_id=None,
                ip="203.0.113.10",
                ip_denylisted=False,
                geo=None,
                device_fingerprint="derived:abc123",
                requested_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                local_hour=hour,
                method="GET",
                path=path,
                sensitivity=classify_path(path),
            )

        assert evaluate(ctx(hour=14)).decision is Decision.ALLOW
        night = evaluate(ctx(hour=23))
        assert night.decision is Decision.CHALLENGE
        assert night.rule == "off_hours"


class TestAdminAuthz:
    """RBAC denial legs: operators and admins only, everyone else 403/401."""

    @pytest.mark.parametrize("role", [UserRole.MEMBER, UserRole.MANAGER])
    def test_tenant_roles_are_refused(self, client, db, role):
        user = _seed_user(db, f"{role.value}@mail.com", role=role)
        target = _seed_user(db, f"target-{role.value}@mail.com")
        resp = client.get(
            f"/api/v1/admin/users/{target.id}/sessions", headers=auth_headers(user)
        )
        assert resp.status_code == 403

    def test_unauthenticated_is_refused(self, client, db):
        resp = client.get(f"/api/v1/admin/users/{uuid.uuid4()}/sessions")
        assert resp.status_code == 401
        resp = client.get("/api/v1/admin/risk-events")
        assert resp.status_code == 401

    def test_member_cannot_terminate(self, client, db):
        member = _seed_user(db, "sneaky@mail.com", role=UserRole.MEMBER)
        victim = _seed_user(db, "victim0@mail.com")
        session, _ = _victim_session(db, victim)
        resp = client.post(
            f"/api/v1/admin/sessions/{session.id}/terminate",
            json={},
            headers=auth_headers(member),
        )
        assert resp.status_code == 403
        db.refresh(session)
        assert session.status == SessionStatus.ACTIVE.value

    def test_platform_operator_is_allowed(self, client, db):
        operator = _seed_user(db, "operator@mail.com", role=UserRole.PLATFORM_OPERATOR)
        target = _seed_user(db, "target-op@mail.com")
        resp = client.get(
            f"/api/v1/admin/users/{target.id}/sessions", headers=auth_headers(operator)
        )
        assert resp.status_code == 200

    def test_admin_is_allowed(self, client, db):
        _, headers = _admin(db)
        target = _seed_user(db, "target-admin@mail.com")
        resp = client.get(f"/api/v1/admin/users/{target.id}/sessions", headers=headers)
        assert resp.status_code == 200


class TestSessionOps:
    def test_list_sessions_with_risk_state(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "listed@mail.com")

        active = UserSession(
            user_id=user.id,
            status=SessionStatus.ACTIVE.value,
            risk_score=50,
            risk_floor=25,
            risk_updated_at=ABAC_EVALUATION_TIME - timedelta(hours=2),
            device_fingerprint="client:known-laptop",
            last_ip="203.0.113.7",
            last_country="KE",
            last_seen_at=ABAC_EVALUATION_TIME - timedelta(minutes=5),
            created_at=ABAC_EVALUATION_TIME - timedelta(hours=3),
        )
        dead = UserSession(
            user_id=user.id,
            status=SessionStatus.TERMINATED.value,
            termination_reason="max session age exceeded",
            created_at=ABAC_EVALUATION_TIME - timedelta(days=2),
        )
        db.add_all([active, dead])
        db.commit()

        resp = client.get(f"/api/v1/admin/users/{user.id}/sessions", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == str(user.id)
        assert [s["id"] for s in body["sessions"]] == [str(active.id), str(dead.id)]

        view = body["sessions"][0]
        assert view["status"] == SessionStatus.ACTIVE.value
        assert view["risk_score"] == 50
        # Decay-adjusted, floored: 50 - 2h * 10/h = 30 >= floor 25.
        assert view["effective_score"] == 30
        assert view["risk_floor"] == 25
        assert view["device_fingerprint"] == "client:known-laptop"
        assert view["last_country"] == "KE"

        assert body["sessions"][1]["termination_reason"] == ("max session age exceeded")

    def test_list_sessions_unknown_user_404(self, client, db):
        _, headers = _admin(db)
        resp = client.get(
            f"/api/v1/admin/users/{uuid.uuid4()}/sessions", headers=headers
        )
        assert resp.status_code == 404

    def test_terminate_is_audited_and_immediate(self, client, db):
        admin, headers = _admin(db)
        victim = _seed_user(db, "victim1@mail.com")
        session, victim_headers = _victim_session(db, victim)

        # The victim's token works before the kill.
        assert (
            client.get("/api/v1/customers", headers=victim_headers).status_code == 200
        )

        resp = client.post(
            f"/api/v1/admin/sessions/{session.id}/terminate",
            json={"reason": "stolen token reported by user"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == SessionStatus.TERMINATED.value
        assert body["termination_reason"].startswith("terminated by operator")

        db.refresh(session)
        assert session.status == SessionStatus.TERMINATED.value

        # Audited with the OPERATOR as actor and the full reason.
        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "session_terminated",
                AuditEvent.entity_id == session.id,
            )
            .all()
        )
        assert events
        assert events[-1].actor_id == admin.id
        assert "stolen token reported by user" in events[-1].after["why"]

        # Immediate: the denylisted sid kills the live access token on the
        # token-validation path itself (no waiting for natural expiry).
        after = client.get("/api/v1/customers", headers=victim_headers)
        assert after.status_code == 401

    def test_terminate_already_terminated_conflicts(self, client, db):
        _, headers = _admin(db)
        victim = _seed_user(db, "victim2@mail.com")
        session, _ = _victim_session(db, victim)
        first = client.post(
            f"/api/v1/admin/sessions/{session.id}/terminate",
            json={},
            headers=headers,
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/v1/admin/sessions/{session.id}/terminate",
            json={},
            headers=headers,
        )
        assert second.status_code == 409

    def test_terminate_unknown_session_404(self, client, db):
        _, headers = _admin(db)
        resp = client.post(
            f"/api/v1/admin/sessions/{uuid.uuid4()}/terminate",
            json={},
            headers=headers,
        )
        assert resp.status_code == 404


class TestBaselineOps:
    def _seed_baseline(self, db, user: User) -> UserBaseline:
        baseline = UserBaseline(
            user_id=user.id,
            known_devices=[
                {"value": "client:known-laptop", "verified_at": FRESH_STAMP},
                {"value": "client:old-tablet", "verified_at": STALE_STAMP},
            ],
            known_countries=[
                {"value": "KE", "verified_at": FRESH_STAMP},
                "US",  # legacy unstamped entry: fresh, no timestamp
            ],
            hour_counts={"9": 40, "10": 60},
            hour_observations=100,
            volume_baselines={
                "confidential": {
                    "ewma": 12.5,
                    "count": 3,
                    "windows": 7,
                    "window_started_at": FRESH_STAMP,
                }
            },
        )
        db.add(baseline)
        db.commit()
        return baseline

    def test_inspect_baseline_freshness_and_histograms(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "inspected@mail.com")
        self._seed_baseline(db, user)

        resp = client.get(f"/api/v1/admin/users/{user.id}/baseline", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["learned"] is True

        devices = {d["value"]: d for d in body["devices"]}
        assert devices["client:known-laptop"]["fresh"] is True
        assert devices["client:old-tablet"]["fresh"] is False, (
            "an entry past the trust TTL must read as aged-out"
        )
        countries = {c["value"]: c for c in body["countries"]}
        assert countries["KE"]["fresh"] is True
        assert countries["US"]["fresh"] is True
        assert countries["US"]["verified_at"] is None, "legacy entries are unstamped"

        assert body["hour_counts"] == {"9": 40, "10": 60}
        assert body["hour_observations"] == 100
        assert body["volume_baselines"]["confidential"]["ewma"] == 12.5
        assert body["volume_baselines"]["confidential"]["windows"] == 7

    def test_inspect_unlearned_user_is_explicit_empty(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "blank@mail.com")
        resp = client.get(f"/api/v1/admin/users/{user.id}/baseline", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["learned"] is False
        assert body["devices"] == [] and body["countries"] == []

    def test_inspect_unknown_user_404(self, client, db):
        _, headers = _admin(db)
        resp = client.get(
            f"/api/v1/admin/users/{uuid.uuid4()}/baseline", headers=headers
        )
        assert resp.status_code == 404

    def test_expire_device_is_audited_and_refires_soft_signal(
        self, client, db, monkeypatch
    ):
        """The full remediation loop the issue demands: expire → the next
        use re-fires new_device (one challenge-grade signal) → a passed
        step-up re-absorbs it as NEW trust and notifies the owner."""
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        admin, headers = _admin(db)
        user = _seed_user(db, "compromised@mail.com")
        baseline = self._seed_baseline(db, user)

        resp = client.delete(
            f"/api/v1/admin/users/{user.id}/baseline/devices",
            params={"value": "client:known-laptop"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["expired"] is True

        db.refresh(baseline)
        assert "client:known-laptop" not in baselines.fresh_values(
            baseline.known_devices, ABAC_EVALUATION_TIME
        ), "the entry must be aged-out, not deleted"
        assert "client:known-laptop" in baselines.entry_values(
            baseline.known_devices
        ), "history is preserved"

        audits = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "baseline",
                AuditEvent.action == "baseline_entry_expired",
            )
            .all()
        )
        assert audits
        assert audits[-1].actor_id == admin.id
        assert audits[-1].after["value"] == "client:known-laptop"
        assert audits[-1].before["was_fresh"] is True

        # Next use of the expired context re-fires the soft signal.
        session, victim_headers = _victim_session(db, user)
        probe = client.get(
            "/api/v1/customers",
            headers={**victim_headers, "X-Device-Fingerprint": "known-laptop"},
        )
        assert probe.status_code == 200, "one soft signal allows + logs"
        db.refresh(session)
        assert session.risk_score >= settings.RISK_SCORE_NEW_DEVICE
        soft = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "soft_signal_new_device",
            )
            .all()
        )
        assert soft, "the expired device must deviate from the baseline again"

        # A passed step-up re-absorbs it as NEW trust → owner notified.
        with (
            patch("app.modules.auth.service.AuthService._send_otp_email") as send,
            patch(
                "app.modules.auth.service.AuthService._send_new_device_alert"
            ) as alert,
        ):
            login = client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": VALID_PASSWORD},
                headers={"X-Device-Fingerprint": "known-laptop"},
            )
            assert login.status_code == 200
            code = send.call_args[0][1]
            verify = client.post(
                "/api/v1/auth/verify-otp",
                json={"email": user.email, "code": code},
                headers={"X-Device-Fingerprint": "known-laptop"},
            )
            assert verify.status_code == 200
        alert.assert_called_once()
        db.refresh(baseline)
        assert "client:known-laptop" in baselines.fresh_values(
            baseline.known_devices, ABAC_EVALUATION_TIME
        ), "re-absorption restores fresh trust"

    def test_expire_country_entry(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "roamer@mail.com")
        baseline = self._seed_baseline(db, user)

        resp = client.delete(
            f"/api/v1/admin/users/{user.id}/baseline/countries",
            params={"value": "US"},
            headers=headers,
        )
        assert resp.status_code == 200
        db.refresh(baseline)
        assert "US" not in baselines.fresh_values(
            baseline.known_countries, ABAC_EVALUATION_TIME
        )

    def test_expire_unknown_entry_404(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "clean@mail.com")
        self._seed_baseline(db, user)
        resp = client.delete(
            f"/api/v1/admin/users/{user.id}/baseline/devices",
            params={"value": "client:never-seen"},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_expire_without_baseline_404(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "nobase@mail.com")
        resp = client.delete(
            f"/api/v1/admin/users/{user.id}/baseline/devices",
            params={"value": "client:x"},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_expire_invalid_kind_422(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "kinds@mail.com")
        self._seed_baseline(db, user)
        resp = client.delete(
            f"/api/v1/admin/users/{user.id}/baseline/gadgets",
            params={"value": "client:x"},
            headers=headers,
        )
        assert resp.status_code == 422


class TestRiskEvents:
    def _seed_events(self, db, user: User) -> None:
        sid = uuid.uuid4()
        rows = [
            AuditEvent(
                actor_id=user.id,
                entity_type="session",
                entity_id=sid,
                action="session_challenged",
                after={"why": "soft clamp", "soft_clamp": True},
                created_at=ABAC_EVALUATION_TIME - timedelta(days=1),
            ),
            AuditEvent(
                actor_id=user.id,
                entity_type="session",
                entity_id=sid,
                action="session_challenged",
                after={"why": "ordinary crossing", "soft_clamp": False},
                created_at=ABAC_EVALUATION_TIME - timedelta(days=2),
            ),
            AuditEvent(
                actor_id=user.id,
                entity_type="session",
                entity_id=sid,
                action="session_terminated",
                after={"why": "risk score 130 crossed terminate threshold"},
                created_at=ABAC_EVALUATION_TIME - timedelta(days=3),
            ),
            AuditEvent(
                actor_id=user.id,
                entity_type="session",
                entity_id=sid,
                action="session_challenged",
                after={"why": "old clamp", "soft_clamp": True},
                created_at=ABAC_EVALUATION_TIME - timedelta(days=30),
            ),
            # Non-session noise that must never appear in the listing.
            AuditEvent(
                actor_id=user.id,
                entity_type="access_decision",
                entity_id=user.id,
                action="policy_challenge",
                after={"rule": "off_hours"},
                created_at=ABAC_EVALUATION_TIME - timedelta(days=1),
            ),
        ]
        db.add_all(rows)
        db.commit()

    def test_lists_session_events_newest_first_with_total(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "events@mail.com")
        self._seed_events(db, user)

        resp = client.get("/api/v1/admin/risk-events", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4, "non-session audit rows must not leak in"
        created = [e["created_at"] for e in body["events"]]
        assert created == sorted(created, reverse=True)
        actions = {e["action"] for e in body["events"]}
        assert "policy_challenge" not in actions

    def test_weekly_soft_clamp_review_query(self, client, db):
        """The exact query ADR-0012's ops section points at."""
        _, headers = _admin(db)
        user = _seed_user(db, "clamps@mail.com")
        self._seed_events(db, user)

        since = (ABAC_EVALUATION_TIME - timedelta(days=7)).isoformat()
        resp = client.get(
            "/api/v1/admin/risk-events",
            params={
                "action": "session_challenged",
                "soft_clamp": "true",
                "since": since,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1, (
            "only the clamped challenge inside the window belongs in the "
            "weekly review queue"
        )
        event = body["events"][0]
        assert event["action"] == "session_challenged"
        assert event["detail"]["soft_clamp"] is True
        assert event["detail"]["why"] == "soft clamp"
        assert event["user_id"] == str(user.id)

    def test_soft_clamp_false_means_explicitly_unclamped(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "unclamped@mail.com")
        self._seed_events(db, user)

        resp = client.get(
            "/api/v1/admin/risk-events",
            params={"soft_clamp": "false"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["events"][0]["detail"]["why"] == "ordinary crossing"

    def test_pagination_reports_honest_totals(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "pages@mail.com")
        self._seed_events(db, user)

        resp = client.get(
            "/api/v1/admin/risk-events",
            params={"limit": 2, "offset": 0},
            headers=headers,
        )
        body = resp.json()
        assert body["total"] == 4
        assert len(body["events"]) == 2
        next_page = client.get(
            "/api/v1/admin/risk-events",
            params={"limit": 2, "offset": 2},
            headers=headers,
        ).json()
        assert next_page["total"] == 4
        assert len(next_page["events"]) == 2
        assert {e["id"] for e in body["events"]}.isdisjoint(
            {e["id"] for e in next_page["events"]}
        )

    def test_filter_by_action(self, client, db):
        _, headers = _admin(db)
        user = _seed_user(db, "kills@mail.com")
        self._seed_events(db, user)

        resp = client.get(
            "/api/v1/admin/risk-events",
            params={"action": "session_terminated"},
            headers=headers,
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["events"][0]["action"] == "session_terminated"
