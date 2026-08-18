"""Tenant lifecycle: owner_profiles.status + audited operator PATCH (#71).

Pins the ADR-0013 Phase A lifecycle contract:

- ``active ⇄ suspended`` only, operator-set via
  ``PATCH /platform/owners/{owner_id}/status``;
- both directions audited (``owner_suspended`` / ``owner_reactivated``)
  with actor and before/after state, in the same transaction;
- 404 on unknown owner ids (never implicit creation), 422 on invalid
  status values;
- tenant roles (ADMIN/MANAGER/MEMBER) are rejected with 403;
- ``users.role`` is never touched (QA finding 09: suspension is org
  state, not a role mutation);
- a no-op (status unchanged) writes no audit row.
"""

import uuid

from app.common.audit import AuditEvent
from app.common.security import create_access_token, hash_password
from app.constants.enums import OwnerStatus, UserRole
from app.modules.auth.models import User
from app.modules.owner.service import OwnerService


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


def _seed_owner(db) -> uuid.UUID:
    profile = OwnerService(db).get_or_create()
    db.commit()
    return profile.id


def _status_url(owner_id: uuid.UUID) -> str:
    return f"/api/v1/platform/owners/{owner_id}/status"


class TestStatusPatch:
    def test_suspend_then_reactivate_roundtrip(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)

        resp = client.patch(
            _status_url(owner_id),
            json={"status": "suspended"},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "suspended"
        assert body["id"] == str(owner_id)

        resp = client.patch(
            _status_url(owner_id),
            json={"status": "active"},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_default_status_is_active(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        resp = client.get("/api/v1/platform/owners", headers=_auth(operator))
        assert resp.status_code == 200
        owners = {o["id"]: o for o in resp.json()["owners"]}
        assert owners[str(owner_id)]["status"] == "active"

    def test_unknown_owner_is_404_and_never_created(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        unknown = uuid.uuid4()
        resp = client.patch(
            _status_url(unknown),
            json={"status": "suspended"},
            headers=_auth(operator),
        )
        assert resp.status_code == 404
        from app.modules.owner.models import OwnerProfile

        assert db.get(OwnerProfile, unknown) is None

    def test_invalid_status_is_422(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        for bad in ("deleted", "", "ACTIVE ", None):
            resp = client.patch(
                _status_url(owner_id),
                json={"status": bad},
                headers=_auth(operator),
            )
            assert resp.status_code == 422, bad

    def test_tenant_roles_are_403(self, client, db):
        owner_id = _seed_owner(db)
        for i, role in enumerate((UserRole.ADMIN, UserRole.MANAGER, UserRole.MEMBER)):
            user = _seed_user(db, f"tenant{i}@mail.com", role)
            resp = client.patch(
                _status_url(owner_id),
                json={"status": "suspended"},
                headers=_auth(user),
            )
            assert resp.status_code == 403, role


class TestStatusAudit:
    def _events(self, db, owner_id: uuid.UUID) -> list[AuditEvent]:
        return (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "owner_profile",
                AuditEvent.entity_id == owner_id,
            )
            .order_by(AuditEvent.created_at)
            .all()
        )

    def test_both_directions_audited_with_actor_and_before_after(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)

        client.patch(
            _status_url(owner_id),
            json={"status": "suspended"},
            headers=_auth(operator),
        )
        client.patch(
            _status_url(owner_id),
            json={"status": "active"},
            headers=_auth(operator),
        )

        events = self._events(db, owner_id)
        assert [e.action for e in events] == ["owner_suspended", "owner_reactivated"]
        suspended, reactivated = events
        assert suspended.actor_id == operator.id
        assert suspended.before == {"status": "active"}
        assert suspended.after == {"status": "suspended"}
        assert reactivated.actor_id == operator.id
        assert reactivated.before == {"status": "suspended"}
        assert reactivated.after == {"status": "active"}

    def test_noop_writes_no_audit_row(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        resp = client.patch(
            _status_url(owner_id),
            json={"status": "active"},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert self._events(db, owner_id) == []

    def test_audit_rolls_back_with_the_status_write(self, db):
        """Service-level: audit + status share one transaction."""
        owner_id = _seed_owner(db)
        service = OwnerService(db)
        service.set_owner_status(owner_id, OwnerStatus.SUSPENDED)
        db.rollback()
        from app.modules.owner.models import OwnerProfile

        assert db.get(OwnerProfile, owner_id).status == "active"
        assert self._events(db, owner_id) == []


class TestQaFinding09:
    def test_suspension_never_touches_users_role(self, client, db):
        """QA 09: suspension is org state — no user role may change."""
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)
        owner_id = _seed_owner(db)

        resp = client.patch(
            _status_url(owner_id),
            json={"status": "suspended"},
            headers=_auth(operator),
        )
        assert resp.status_code == 200

        db.expire_all()
        roles = {u.email: u.role for u in db.query(User).order_by(User.email).all()}
        assert roles == {
            "admin@mail.com": "admin",
            "member@mail.com": "member",
            "operator@mail.com": "platform_operator",
        }
        assert db.get(User, admin.id).is_active is True
        assert db.get(User, member.id).is_active is True
        assert db.get(User, operator.id).is_active is True
