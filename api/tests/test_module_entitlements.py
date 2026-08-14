"""Per-owner module entitlements — operator-granted (ADR-0011, issue #58).

Pins the entitlement contract:

- default-on: with no ``owner_module_settings`` row every module is enabled
  (service map and listings agree);
- a disabled module's router rejects every request with 403;
- essential modules (auth, owner, health, dashboard) can never be disabled
  (422) and carry no router gate;
- entitlements are GRANTS: only a PLATFORM_OPERATOR may write, via
  ``PATCH /platform/owners/{owner_id}/modules/{module_key}``; the tenant
  self-service ``PATCH /owner/modules/{module_key}`` no longer exists;
- ``GET /owner/modules`` stays the tenant admin's READ-ONLY view;
- the platform surface is owner-id-scoped and 404s on unknown owners
  (never get-or-create);
- the operator role is isolated from tenant data (403 on tenant gates);
- every change is audited and the bootstrap ``enabledModules`` map follows.
"""

import uuid

from app.common.audit import AuditEvent
from app.common.security import create_access_token, hash_password
from app.constants.enums import ESSENTIAL_MODULES, ModuleKey, UserRole
from app.modules.auth.models import User
from app.modules.owner.service import OwnerService

_TOGGLEABLE = [k for k in ModuleKey if k not in ESSENTIAL_MODULES]


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
    """Create the owner profile row and return its id (the platform surface
    never creates owners implicitly)."""
    profile = OwnerService(db).get_or_create()
    db.commit()
    return profile.id


def _modules_url(owner_id: uuid.UUID, module_key: str | None = None) -> str:
    base = f"/api/v1/platform/owners/{owner_id}/modules"
    return f"{base}/{module_key}" if module_key else base


class TestDefaultEnabled:
    def test_no_rows_means_every_module_enabled(self, db):
        svc = OwnerService(db)
        enabled = svc.enabled_modules_map()
        assert set(enabled) == {k.value for k in ModuleKey}
        assert all(enabled.values()), "default must be ENABLED for every module"

    def test_admin_listing_shows_all_keys_enabled_with_essential_flags(
        self, client, db
    ):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        resp = client.get("/api/v1/owner/modules", headers=_auth(admin))
        assert resp.status_code == 200
        modules = {m["moduleKey"]: m for m in resp.json()["modules"]}
        assert set(modules) == {k.value for k in ModuleKey}
        for key in ModuleKey:
            assert modules[key.value]["enabled"] is True
            assert modules[key.value]["essential"] is (key in ESSENTIAL_MODULES)

    def test_operator_listing_matches_admin_view(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        resp = client.get(_modules_url(owner_id), headers=_auth(operator))
        assert resp.status_code == 200
        modules = {m["moduleKey"]: m for m in resp.json()["modules"]}
        assert set(modules) == {k.value for k in ModuleKey}
        assert all(m["enabled"] for m in modules.values())

    def test_gated_router_accessible_by_default(self, client, db):
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)
        resp = client.get("/api/v1/customers", headers=_auth(member))
        assert resp.status_code == 200


class TestDisabledModuleReturns403:
    def test_disabled_module_router_rejects_with_403(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)
        owner_id = _seed_owner(db)

        resp = client.patch(
            _modules_url(owner_id, "customers"),
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "moduleKey": "customers",
            "enabled": False,
            "essential": False,
        }

        blocked = client.get("/api/v1/customers", headers=_auth(member))
        assert blocked.status_code == 403
        assert "disabled" in blocked.json()["error"].lower()

        # Even the tenant admin is blocked on the module's own routes — the
        # gate is org state (an operator grant), not a per-user permission.
        assert client.get("/api/v1/customers", headers=_auth(admin)).status_code == 403

    def test_reenabling_restores_access(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)
        for enabled, expected in ((False, 403), (True, 200)):
            resp = client.patch(
                _modules_url(owner_id, "reports"),
                json={"enabled": enabled},
                headers=_auth(operator),
            )
            assert resp.status_code == 200
            assert (
                client.get("/api/v1/reports/sales", headers=_auth(admin)).status_code
                == expected
            )

    def test_other_modules_unaffected_by_one_disable(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)
        resp = client.patch(
            _modules_url(owner_id, "vendors"),
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert client.get("/api/v1/customers", headers=_auth(admin)).status_code == 200

    def test_bootstrap_enabled_modules_map_follows(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)
        client.patch(
            _modules_url(owner_id, "quotes"),
            json={"enabled": False},
            headers=_auth(operator),
        )
        profile = client.get("/api/v1/owner", headers=_auth(admin))
        assert profile.status_code == 200
        enabled_modules = profile.json()["enabledModules"]
        assert enabled_modules["quotes"] is False
        assert enabled_modules["invoices"] is True


class TestSalesDeskModule:
    """The Sales Desk (!46) is toggleable like any other business module."""

    def test_sales_desk_listed_in_admin_modules(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        resp = client.get("/api/v1/owner/modules", headers=_auth(admin))
        assert resp.status_code == 200
        modules = {m["moduleKey"]: m for m in resp.json()["modules"]}
        assert "sales_desk" in modules
        assert modules["sales_desk"]["enabled"] is True
        assert modules["sales_desk"]["essential"] is False

    def test_disabling_sales_desk_rejects_router_with_403(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)
        owner_id = _seed_owner(db)
        headers = _auth(admin)

        resp = client.patch(
            _modules_url(owner_id, "sales_desk"),
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "moduleKey": "sales_desk",
            "enabled": False,
            "essential": False,
        }

        blocked = client.get("/api/v1/sales-desk/dashboard", headers=_auth(member))
        assert blocked.status_code == 403
        assert "disabled" in blocked.json()["error"].lower()

        # Deals/nurture/onboarding keep their own independent keys: disabling
        # the Sales Desk shell does not disable the underlying deals module.
        assert client.get("/api/v1/deals", headers=headers).status_code == 200

        # Re-enabling restores access. The successful GET comes LAST: the
        # sales-desk service opens a read-only REPEATABLE READ snapshot on
        # the request session (like reports), so no write may follow it.
        resp = client.patch(
            _modules_url(owner_id, "sales_desk"),
            json={"enabled": True},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert (
            client.get("/api/v1/sales-desk/dashboard", headers=headers).status_code
            == 200
        )


class TestEssentialModules:
    def test_disabling_essential_module_is_422(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        for key in sorted(k.value for k in ESSENTIAL_MODULES):
            resp = client.patch(
                _modules_url(owner_id, key),
                json={"enabled": False},
                headers=_auth(operator),
            )
            assert resp.status_code == 422, key
            assert "essential" in resp.json()["error"].lower()

    def test_unknown_module_key_is_422(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        resp = client.patch(
            _modules_url(owner_id, "not_a_module"),
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 422


class TestPlatformOwnerScoping:
    """The operator surface is owner-id-scoped and never auto-creates."""

    def test_unknown_owner_id_is_404_on_read_and_write(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        unknown = uuid.uuid4()
        assert (
            client.get(_modules_url(unknown), headers=_auth(operator)).status_code
            == 404
        )
        resp = client.patch(
            _modules_url(unknown, "customers"),
            json={"enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 404

    def test_owners_listing_is_operator_only(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        owner_id = _seed_owner(db)

        resp = client.get("/api/v1/platform/owners", headers=_auth(operator))
        assert resp.status_code == 200
        owners = resp.json()["owners"]
        assert [o["id"] for o in owners] == [str(owner_id)]
        assert owners[0]["fullName"]

        assert (
            client.get("/api/v1/platform/owners", headers=_auth(admin)).status_code
            == 403
        )


class TestAuthorization:
    """Entitlements are grants: only the platform operator writes."""

    def test_non_admins_cannot_read_owner_module_settings(self, client, db):
        for email, role in (
            ("member@mail.com", UserRole.MEMBER),
            ("manager@mail.com", UserRole.MANAGER),
        ):
            user = _seed_user(db, email, role)
            resp = client.get("/api/v1/owner/modules", headers=_auth(user))
            assert resp.status_code == 403, role

    def test_tenant_self_service_patch_route_is_gone(self, client, db):
        """The QA-09 hole: the tenant admin could re-enable anything."""
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        resp = client.patch(
            "/api/v1/owner/modules/customers",
            json={"enabled": True},
            headers=_auth(admin),
        )
        assert resp.status_code == 404

    def test_tenant_roles_cannot_write_platform_entitlements(self, client, db):
        owner_id = _seed_owner(db)
        for email, role in (
            ("member@mail.com", UserRole.MEMBER),
            ("manager@mail.com", UserRole.MANAGER),
            ("admin@mail.com", UserRole.ADMIN),
        ):
            user = _seed_user(db, email, role)
            resp = client.patch(
                _modules_url(owner_id, "customers"),
                json={"enabled": False},
                headers=_auth(user),
            )
            assert resp.status_code == 403, role

    def test_operator_is_isolated_from_tenant_gates(self, client, db):
        """PLATFORM_OPERATOR is not a tenant role: tenant-scoped role gates
        (require_role(ADMIN), privileged reports) reject it (ADR-0011)."""
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        assert not UserRole.PLATFORM_OPERATOR.is_privileged
        assert (
            client.get("/api/v1/owner/modules", headers=_auth(operator)).status_code
            == 403
        )
        assert (
            client.get("/api/v1/reports/sales", headers=_auth(operator)).status_code
            == 403
        )

    def test_unauthenticated_cannot_read_or_write(self, client, db):
        owner_id = _seed_owner(db)
        assert client.get("/api/v1/owner/modules").status_code == 401
        assert client.get(_modules_url(owner_id)).status_code == 401
        assert (
            client.patch(
                _modules_url(owner_id, "customers"), json={"enabled": False}
            ).status_code
            == 401
        )


class TestAudit:
    def test_toggle_writes_audit_event(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        owner_id = _seed_owner(db)
        client.patch(
            _modules_url(owner_id, "expenses"),
            json={"enabled": False},
            headers=_auth(operator),
        )
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.entity_type == "owner_module_setting")
            .all()
        )
        assert len(events) == 1
        event = events[0]
        assert event.action == "module_disabled"
        assert str(event.actor_id) == str(operator.id)
        assert event.before == {"module_key": "expenses", "enabled": True}
        assert event.after == {"module_key": "expenses", "enabled": False}
