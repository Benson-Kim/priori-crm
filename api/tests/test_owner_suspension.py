"""Suspension enforcement (ADR-0014 Phase A, #71).

Pins the two enforcement seams of the `owner_profiles.status` lifecycle:

1. Module gate (``require_module``): a suspended owner is denied every
   non-essential module with a clear 403; essential modules (auth, owner,
   health, dashboard) keep serving existing sessions.
2. Token issuance (``AuthService._ensure_owner_not_suspended``): both
   minting points — verify-otp and refresh — reject non-operator users
   with a clear 403 while suspended. The platform operator can always
   sign in (it must be able to reactivate the org).

Suspension is reversible and non-destructive: reactivation restores
everything, the un-burnt OTP included, and no ``users`` row is touched.
"""

from unittest.mock import patch

from app.common.security import create_access_token, hash_password
from app.constants.enums import OwnerStatus, UserRole
from app.modules.auth.models import User
from app.modules.owner.service import OwnerService

VALID_PASSWORD = "Sup3r!Secret1"


def _seed_user(db, email: str, role: UserRole = UserRole.MEMBER) -> User:
    user = User(
        email=email,
        password_hash=hash_password(VALID_PASSWORD),
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


def _set_owner_status(db, status: OwnerStatus) -> None:
    profile = OwnerService(db).get_or_create()
    profile.status = status.value
    db.commit()


def _login_and_capture_otp(client, email: str) -> str:
    with patch("app.modules.auth.service.AuthService._send_otp_email") as send:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": VALID_PASSWORD},
        )
    assert resp.status_code == 200
    return send.call_args[0][1]


class TestModuleGateDenial:
    def test_suspended_denies_every_nonessential_module(self, client, db):
        member = _seed_user(db, "member@mail.com")
        _set_owner_status(db, OwnerStatus.SUSPENDED)
        headers = _auth(member)
        for path in (
            "/api/v1/customers",
            "/api/v1/invoices",
            "/api/v1/quotes",
            "/api/v1/vendors",
            "/api/v1/reports/sales",
        ):
            resp = client.get(path, headers=headers)
            assert resp.status_code == 403, path
            assert "suspended" in resp.json()["error"].lower(), path

    def test_essential_surfaces_keep_serving_existing_sessions(self, client, db):
        admin = _seed_user(db, "admin@mail.com", UserRole.ADMIN)
        _set_owner_status(db, OwnerStatus.SUSPENDED)
        headers = _auth(admin)
        # owner profile (essential: owner) and dashboard (essential) stay up.
        assert client.get("/api/v1/owner", headers=headers).status_code == 200
        assert (
            client.get("/api/v1/dashboard/summary", headers=headers).status_code == 200
        )
        # health stays up (unauthenticated infrastructure probe).
        assert client.get("/api/v1/health").status_code == 200

    def test_reactivation_restores_module_access(self, client, db):
        member = _seed_user(db, "member@mail.com")
        _set_owner_status(db, OwnerStatus.SUSPENDED)
        assert client.get("/api/v1/customers", headers=_auth(member)).status_code == 403
        _set_owner_status(db, OwnerStatus.ACTIVE)
        assert client.get("/api/v1/customers", headers=_auth(member)).status_code == 200

    def test_scheduled_transition_endpoints_denied_while_suspended(
        self, client, db, monkeypatch
    ):
        """Verified behaviour, documented in the platform-operations runbook:

        The nightly transition endpoints live on non-essential gated routers
        (invoices/expenses/quotes), so suspension denies them like any other
        request to those modules — the scheduler gets a 403, no documents
        transition. (The transitions themselves are pure bulk UPDATEs and
        enqueue no tenant email — verified in
        InvoiceService/ExpenseService.bulk_transition_overdue and
        QuoteService.bulk_transition_expired.)
        """
        from app.lib.config import settings

        monkeypatch.setattr(settings, "INTERNAL_API_SECRET", "test-internal-secret")
        _seed_user(db, "member@mail.com")
        _set_owner_status(db, OwnerStatus.SUSPENDED)
        for path in (
            "/api/v1/invoices/internal/transition-overdue",
            "/api/v1/expenses/internal/transition-overdue",
            "/api/v1/quotes/internal/transition-expired",
        ):
            resp = client.post(
                path, headers={"X-Internal-Secret": "test-internal-secret"}
            )
            assert resp.status_code == 403, path
            assert "suspended" in resp.json()["error"].lower(), path


class TestTokenIssuanceDenial:
    def test_verify_otp_rejected_with_clear_403_while_suspended(self, client, db):
        _seed_user(db, "member@mail.com")
        _set_owner_status(db, OwnerStatus.SUSPENDED)
        code = _login_and_capture_otp(client, "member@mail.com")
        resp = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "member@mail.com", "code": code},
        )
        assert resp.status_code == 403
        assert "suspended" in resp.json()["error"].lower()

    def test_otp_not_burnt_reactivation_lets_same_code_succeed(self, client, db):
        _seed_user(db, "member@mail.com")
        _set_owner_status(db, OwnerStatus.SUSPENDED)
        code = _login_and_capture_otp(client, "member@mail.com")
        assert (
            client.post(
                "/api/v1/auth/verify-otp",
                json={"email": "member@mail.com", "code": code},
            ).status_code
            == 403
        )
        _set_owner_status(db, OwnerStatus.ACTIVE)
        resp = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "member@mail.com", "code": code},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_rejected_while_suspended_and_token_not_spent(self, client, db):
        _seed_user(db, "member@mail.com")
        code = _login_and_capture_otp(client, "member@mail.com")
        tokens = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "member@mail.com", "code": code},
        ).json()

        _set_owner_status(db, OwnerStatus.SUSPENDED)
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 403
        assert "suspended" in resp.json()["error"].lower()

        # The gate runs before rotation spends the token: reactivation lets
        # the SAME refresh token succeed (no forced re-login).
        _set_owner_status(db, OwnerStatus.ACTIVE)
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_operator_sign_in_unaffected_by_suspension(self, client, db):
        _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        _set_owner_status(db, OwnerStatus.SUSPENDED)
        code = _login_and_capture_otp(client, "operator@mail.com")
        resp = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "operator@mail.com", "code": code},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_no_owner_profile_row_means_active(self, client, db):
        """A deployment whose profile was never created is not suspended."""
        _seed_user(db, "member@mail.com")
        code = _login_and_capture_otp(client, "member@mail.com")
        resp = client.post(
            "/api/v1/auth/verify-otp",
            json={"email": "member@mail.com", "code": code},
        )
        assert resp.status_code == 200
