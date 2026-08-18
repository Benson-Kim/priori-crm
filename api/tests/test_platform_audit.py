"""Operator audit read surface: GET /platform/audit (#71, ADR-0014 Phase A).

Pins the contract:

- operator-only (tenant roles 403), never module-gated (works while the
  owner is suspended and every toggleable module disabled);
- scoped to exactly the entity types the owner service writes:
  ``owner_module_setting`` and ``owner_profile``
  (``PLATFORM_AUDIT_ENTITY_TYPES``) — foreign audit rows (invoices etc.)
  never leak in;
- newest first, paginated with the shared window pagination;
- filterable by owner (lifecycle events directly, entitlement events via
  the setting row's owner FK), action, actor and date range.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.common.audit import record_audit_event
from app.common.security import create_access_token, hash_password
from app.constants.enums import UserRole
from app.modules.auth.models import User
from app.modules.owner.models import OwnerProfile
from app.modules.owner.service import OwnerService

AUDIT_URL = "/api/v1/platform/audit"


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


def _seed_second_owner(db) -> uuid.UUID:
    profile = OwnerProfile(id=uuid.uuid4(), full_name="Second Org")
    db.add(profile)
    db.commit()
    return profile.id


def _drive_events(client, db, operator, owner_id) -> None:
    """One entitlement disable + one suspension via the audited APIs."""
    resp = client.patch(
        f"/api/v1/platform/owners/{owner_id}/modules/customers",
        json={"enabled": False},
        headers=_auth(operator),
    )
    assert resp.status_code == 200
    resp = client.patch(
        f"/api/v1/platform/owners/{owner_id}/status",
        json={"status": "suspended"},
        headers=_auth(operator),
    )
    assert resp.status_code == 200


class TestAccessControl:
    def test_tenant_roles_are_403(self, client, db):
        for i, role in enumerate((UserRole.ADMIN, UserRole.MANAGER, UserRole.MEMBER)):
            user = _seed_user(db, f"tenant{i}@mail.com", role)
            assert client.get(AUDIT_URL, headers=_auth(user)).status_code == 403, role

    def test_operator_reads_audit_even_while_owner_suspended(self, client, db):
        """/platform is never module-gated: platform administration must
        keep working when the tenant is fully locked down."""
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        _drive_events(client, db, operator, owner_id)
        resp = client.get(AUDIT_URL, headers=_auth(operator))
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2


class TestScopeAndOrdering:
    def test_only_owner_service_entity_types_newest_first(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        _drive_events(client, db, operator, owner_id)
        # A foreign audit row (invoice payment) must never leak in.
        record_audit_event(
            db,
            actor_id=operator.id,
            entity_type="invoice",
            entity_id=uuid.uuid4(),
            action="payment_recorded",
        )
        db.commit()

        resp = client.get(AUDIT_URL, headers=_auth(operator))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert [i["action"] for i in items] == ["owner_suspended", "module_disabled"]
        assert {i["entityType"] for i in items} == {
            "owner_profile",
            "owner_module_setting",
        }
        suspended = items[0]
        assert suspended["actorId"] == str(operator.id)
        assert suspended["before"] == {"status": "active"}
        assert suspended["after"] == {"status": "suspended"}

    def test_pagination_window(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        _drive_events(client, db, operator, owner_id)

        resp = client.get(
            AUDIT_URL,
            params={"per_page": 1, "withTotal": True},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["metadata"]["total"] == 2
        assert body["metadata"]["has_next"] is True
        assert body["metadata"]["has_prev"] is False

        resp = client.get(
            AUDIT_URL,
            params={"per_page": 1, "page": 2},
            headers=_auth(operator),
        )
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["metadata"]["has_next"] is False
        assert body["metadata"]["has_prev"] is True


class TestFilters:
    def test_owner_filter_covers_both_entity_types(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        other_id = _seed_second_owner(db)
        _drive_events(client, db, operator, owner_id)
        # Events against the second owner must be excluded by the filter.
        resp = client.patch(
            f"/api/v1/platform/owners/{other_id}/modules/reports",
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 200

        resp = client.get(
            AUDIT_URL,
            params={"ownerId": str(owner_id)},
            headers=_auth(operator),
        )
        items = resp.json()["items"]
        assert [i["action"] for i in items] == ["owner_suspended", "module_disabled"]

        resp = client.get(
            AUDIT_URL,
            params={"ownerId": str(other_id)},
            headers=_auth(operator),
        )
        items = resp.json()["items"]
        assert [i["action"] for i in items] == ["module_disabled"]
        assert items[0]["before"] == {"module_key": "reports", "enabled": True}

    def test_action_filter(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        _drive_events(client, db, operator, owner_id)
        resp = client.get(
            AUDIT_URL,
            params={"action": "owner_suspended"},
            headers=_auth(operator),
        )
        items = resp.json()["items"]
        assert [i["action"] for i in items] == ["owner_suspended"]

    def test_actor_filter(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        other_actor = uuid.uuid4()
        owner_id = _seed_owner(db)
        _drive_events(client, db, operator, owner_id)
        record_audit_event(
            db,
            actor_id=other_actor,
            entity_type="owner_profile",
            entity_id=owner_id,
            action="owner_reactivated",
        )
        db.commit()

        resp = client.get(
            AUDIT_URL,
            params={"actorId": str(operator.id)},
            headers=_auth(operator),
        )
        assert {i["actorId"] for i in resp.json()["items"]} == {str(operator.id)}
        assert len(resp.json()["items"]) == 2

    def test_date_range_filter(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        _drive_events(client, db, operator, owner_id)

        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

        resp = client.get(
            AUDIT_URL,
            params={"dateFrom": past, "dateTo": future},
            headers=_auth(operator),
        )
        assert len(resp.json()["items"]) == 2

        resp = client.get(
            AUDIT_URL, params={"dateFrom": future}, headers=_auth(operator)
        )
        assert resp.json()["items"] == []

        resp = client.get(AUDIT_URL, params={"dateTo": past}, headers=_auth(operator))
        assert resp.json()["items"] == []
