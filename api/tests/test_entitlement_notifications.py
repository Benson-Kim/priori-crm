"""Entitlement-change notifications to tenant admins (#71, ADR-0005 outbox).

Pins the notification contract of ``set_module_enabled_for_owner``:

- one outbox row per ACTIVE tenant admin, enqueued in the SAME
  transaction as the entitlement write (rollback leaves no row);
- content names the module, the new state, the actor's ROLE (never the
  operator's email address) and a timestamp;
- no notification when the write does not change the resolved state
  (idempotent re-apply), and none to inactive admins or non-admin roles;
- delivery is the drainer's job — enqueue does no network I/O.
"""

import uuid

from app.common.email_outbox import EmailOutbox, EmailOutboxStatus
from app.common.security import create_access_token, hash_password
from app.constants.enums import ModuleKey, UserRole
from app.modules.auth.models import User
from app.modules.owner.service import OwnerService


def _seed_user(db, email: str, role: UserRole, is_active: bool = True) -> User:
    user = User(
        email=email,
        password_hash=hash_password("Sup3r!Secret"),
        first_name="Test",
        last_name="User",
        role=role.value,
        is_active=is_active,
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


def _outbox_rows(db) -> list[EmailOutbox]:
    return db.query(EmailOutbox).order_by(EmailOutbox.recipient).all()


class TestEnqueue:
    def test_disable_notifies_every_active_admin_with_role_not_email(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        _seed_user(db, "admin1@mail.com", UserRole.ADMIN)
        _seed_user(db, "admin2@mail.com", UserRole.ADMIN)
        _seed_user(db, "inactive-admin@mail.com", UserRole.ADMIN, is_active=False)
        _seed_user(db, "manager@mail.com", UserRole.MANAGER)
        _seed_user(db, "member@mail.com", UserRole.MEMBER)
        owner_id = _seed_owner(db)

        resp = client.patch(
            f"/api/v1/platform/owners/{owner_id}/modules/customers",
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 200

        rows = _outbox_rows(db)
        assert [r.recipient for r in rows] == ["admin1@mail.com", "admin2@mail.com"]
        for row in rows:
            assert row.status == EmailOutboxStatus.PENDING
            assert "disabled" in row.subject.lower()
            assert "customers" in row.body_text.lower()
            assert "platform operator" in row.body_text
            # The operator's identity must never leak to the tenant.
            assert "operator@mail.com" not in row.body_text
            assert "operator@mail.com" not in row.subject
            # Timestamp line present.
            assert "When:" in row.body_text

    def test_enable_notifies_with_enabled_state(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        _seed_user(db, "admin1@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)
        client.patch(
            f"/api/v1/platform/owners/{owner_id}/modules/reports",
            json={"enabled": False},
            headers=_auth(operator),
        )
        resp = client.patch(
            f"/api/v1/platform/owners/{owner_id}/modules/reports",
            json={"enabled": True},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        rows = _outbox_rows(db)
        assert len(rows) == 2
        assert "disabled" in rows[0].subject.lower()
        assert "enabled" in rows[1].subject.lower()

    def test_no_state_change_means_no_notification(self, client, db):
        """Re-applying the current resolved state must not spam admins."""
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        _seed_user(db, "admin1@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)
        # Default is enabled; an explicit enable changes nothing.
        resp = client.patch(
            f"/api/v1/platform/owners/{owner_id}/modules/customers",
            json={"enabled": True},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert _outbox_rows(db) == []

    def test_no_admins_is_a_quiet_noop(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        resp = client.patch(
            f"/api/v1/platform/owners/{owner_id}/modules/customers",
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert _outbox_rows(db) == []


class TestTransactionality:
    def test_row_created_in_transaction_before_commit(self, db):
        """The enqueue flushes within the caller's open transaction."""
        _seed_user(db, "admin1@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)
        service = OwnerService(db)

        service.set_module_enabled_for_owner(owner_id, ModuleKey.CUSTOMERS, False)
        # Not yet committed: the row is visible on THIS session only.
        assert len(_outbox_rows(db)) == 1

    def test_rollback_leaves_no_outbox_row(self, db):
        """ADR-0005: a rolled-back entitlement change never emails anyone."""
        _seed_user(db, "admin1@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)
        service = OwnerService(db)

        service.set_module_enabled_for_owner(owner_id, ModuleKey.CUSTOMERS, False)
        db.rollback()

        assert _outbox_rows(db) == []
        # ... and the entitlement change itself is gone with it.
        assert OwnerService(db).enabled_modules_map()["customers"] is True
