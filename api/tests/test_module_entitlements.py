"""Per-owner module entitlements (feature toggles).

Pins the entitlement contract:

- default-on: with no ``owner_module_settings`` row every module is enabled
  (service map and admin listing agree);
- a disabled module's router rejects every request with 403;
- essential modules (auth, owner, health, dashboard) can never be disabled
  (422) and carry no router gate;
- GET/PATCH /owner/modules are ADMIN-only;
- every change is audited and the bootstrap ``enabledModules`` map follows.
"""

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

    def test_gated_router_accessible_by_default(self, client, db):
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)
        resp = client.get("/api/v1/customers", headers=_auth(member))
        assert resp.status_code == 200


class TestDisabledModuleReturns403:
    def test_disabled_module_router_rejects_with_403(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)

        resp = client.patch(
            "/api/v1/owner/modules/customers",
            json={"enabled": False},
            headers=_auth(admin),
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

        # Even the admin is blocked on the module's own routes — the gate is
        # org state, not a per-user permission.
        assert client.get("/api/v1/customers", headers=_auth(admin)).status_code == 403

    def test_reenabling_restores_access(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        headers = _auth(admin)
        for enabled, expected in ((False, 403), (True, 200)):
            resp = client.patch(
                "/api/v1/owner/modules/reports",
                json={"enabled": enabled},
                headers=headers,
            )
            assert resp.status_code == 200
            assert (
                client.get("/api/v1/reports/sales", headers=headers).status_code
                == expected
            )

    def test_other_modules_unaffected_by_one_disable(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        headers = _auth(admin)
        resp = client.patch(
            "/api/v1/owner/modules/vendors",
            json={"enabled": False},
            headers=headers,
        )
        assert resp.status_code == 200
        assert client.get("/api/v1/customers", headers=headers).status_code == 200

    def test_bootstrap_enabled_modules_map_follows(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        headers = _auth(admin)
        client.patch(
            "/api/v1/owner/modules/quotes",
            json={"enabled": False},
            headers=headers,
        )
        profile = client.get("/api/v1/owner", headers=headers)
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
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        member = _seed_user(db, "member@mail.com", UserRole.MEMBER)
        headers = _auth(admin)

        assert (
            client.get("/api/v1/sales-desk/dashboard", headers=headers).status_code
            == 200
        )

        resp = client.patch(
            "/api/v1/owner/modules/sales_desk",
            json={"enabled": False},
            headers=headers,
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


class TestEssentialModules:
    def test_disabling_essential_module_is_422(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        for key in sorted(k.value for k in ESSENTIAL_MODULES):
            resp = client.patch(
                f"/api/v1/owner/modules/{key}",
                json={"enabled": False},
                headers=_auth(admin),
            )
            assert resp.status_code == 422, key
            assert "essential" in resp.json()["error"].lower()

    def test_unknown_module_key_is_422(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        resp = client.patch(
            "/api/v1/owner/modules/not_a_module",
            json={"enabled": False},
            headers=_auth(admin),
        )
        assert resp.status_code == 422


class TestAdminOnlyAccess:
    def test_non_admins_cannot_read_module_settings(self, client, db):
        for email, role in (
            ("member@mail.com", UserRole.MEMBER),
            ("manager@mail.com", UserRole.MANAGER),
        ):
            user = _seed_user(db, email, role)
            resp = client.get("/api/v1/owner/modules", headers=_auth(user))
            assert resp.status_code == 403, role

    def test_non_admins_cannot_toggle_modules(self, client, db):
        for email, role in (
            ("member@mail.com", UserRole.MEMBER),
            ("manager@mail.com", UserRole.MANAGER),
        ):
            user = _seed_user(db, email, role)
            resp = client.patch(
                "/api/v1/owner/modules/customers",
                json={"enabled": False},
                headers=_auth(user),
            )
            assert resp.status_code == 403, role

    def test_unauthenticated_cannot_read_module_settings(self, client):
        assert client.get("/api/v1/owner/modules").status_code == 401


class TestAudit:
    def test_toggle_writes_audit_event(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        client.patch(
            "/api/v1/owner/modules/expenses",
            json={"enabled": False},
            headers=_auth(admin),
        )
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.entity_type == "owner_module_setting")
            .all()
        )
        assert len(events) == 1
        event = events[0]
        assert event.action == "module_disabled"
        assert str(event.actor_id) == str(admin.id)
        assert event.before == {"module_key": "expenses", "enabled": True}
        assert event.after == {"module_key": "expenses", "enabled": False}
