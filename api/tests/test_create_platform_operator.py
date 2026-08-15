"""Bootstrap seed script for the platform operator (issue #62, ADR-0011).

Pins the out-of-band operator provisioning contract:

- the script upserts exactly one ``users`` row with
  ``role='platform_operator'``: re-running with the same email is an
  update (password rotation / break-glass), never a duplicate;
- the stored role value is inside the migration's ``ck_users_valid_role``
  IN-list, so the insert satisfies the production CHECK constraint;
- the created operator can authenticate through the real login → OTP flow
  and call ``GET /platform/owners``;
- the operator stays rejected by tenant gates (403) — same isolation pins
  as test_module_entitlements (ADR-0011 disjoint axes);
- an existing tenant user's role is never changed without ``--force-role``;
- credentials are never accepted as CLI arguments, the password is never
  echoed/stored in plaintext, and a missing DATABASE_URL is a clear
  non-zero exit (QA-09: bootstrap is deliberately not API-reachable).
"""

import contextlib
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.common.security import verify_password
from app.constants.enums import UserRole
from app.modules.auth.models import User
from app.modules.owner.service import OwnerService
from app.scripts.create_platform_operator import (
    ENV_EMAIL,
    ENV_PASSWORD,
    RoleConflictError,
    _parse_args,
    main,
    upsert_platform_operator,
)

EMAIL = "ops@platform.example"
PASSWORD = "Op3rator!Secret"
API_DIR = Path(__file__).resolve().parents[1]
MIGRATION = (
    API_DIR
    / "alembic"
    / "versions"
    / "2026_08_14_1200-d1e2f3a4b5c6_add_platform_operator_role.py"
)


def _operator_rows(db):
    return db.query(User).filter(User.email == EMAIL).all()


class TestUpsert:
    def test_creates_single_operator_row_with_hashed_password(self, db):
        user, action = upsert_platform_operator(db, EMAIL, PASSWORD)
        db.commit()

        assert action == "created"
        rows = _operator_rows(db)
        assert len(rows) == 1
        assert rows[0].role == UserRole.PLATFORM_OPERATOR.value
        assert rows[0].is_active is True
        # bcrypt hash, never the plaintext
        assert rows[0].password_hash != PASSWORD
        assert PASSWORD not in rows[0].password_hash
        assert verify_password(PASSWORD, rows[0].password_hash)
        assert user.id == rows[0].id

    def test_rerun_same_email_is_update_never_duplicate(self, db):
        first, action1 = upsert_platform_operator(db, EMAIL, PASSWORD)
        db.commit()
        first_id = first.id

        second, action2 = upsert_platform_operator(db, EMAIL, "N3w!Password")
        db.commit()

        assert (action1, action2) == ("created", "updated")
        rows = _operator_rows(db)
        assert len(rows) == 1
        assert second.id == first_id
        # rotation: the new password is live, the old one is gone
        assert verify_password("N3w!Password", rows[0].password_hash)
        assert not verify_password(PASSWORD, rows[0].password_hash)
        assert rows[0].role == UserRole.PLATFORM_OPERATOR.value

    def test_update_reactivates_deactivated_operator(self, db):
        """Break-glass path: a re-run restores access to a disabled account."""
        user, _ = upsert_platform_operator(db, EMAIL, PASSWORD)
        user.is_active = False
        db.commit()

        upsert_platform_operator(db, EMAIL, PASSWORD)
        db.commit()
        assert _operator_rows(db)[0].is_active is True

    def test_refuses_existing_tenant_user_without_force_role(self, db):
        admin = User(
            email=EMAIL,
            password_hash="irrelevant-hash",
            first_name="Tenant",
            last_name="Admin",
            role=UserRole.ADMIN.value,
        )
        db.add(admin)
        db.commit()

        with pytest.raises(RoleConflictError, match="--force-role"):
            upsert_platform_operator(db, EMAIL, PASSWORD)
        db.rollback()
        assert _operator_rows(db)[0].role == UserRole.ADMIN.value

        _, action = upsert_platform_operator(db, EMAIL, PASSWORD, force_role=True)
        db.commit()
        assert action == "promoted"
        rows = _operator_rows(db)
        assert len(rows) == 1
        assert rows[0].role == UserRole.PLATFORM_OPERATOR.value

    def test_rejects_policy_violating_password_and_bad_email(self, db):
        # LoginRequest enforces the same policy, so a weaker password would
        # seed an account that can never sign in.
        with pytest.raises(ValueError, match=r"[Pp]assword"):
            upsert_platform_operator(db, EMAIL, "short1")
        with pytest.raises(ValueError, match="email"):
            upsert_platform_operator(db, "not-an-email", PASSWORD)
        assert db.query(User).count() == 0


class TestRoleConstraint:
    def test_role_value_is_in_migration_check_in_list(self):
        """The stored value must satisfy the production ``ck_users_valid_role``
        CHECK constraint (the test schema is built by create_all, so the
        migration's IN-list is pinned textually, like the billing-profiles
        migration test)."""
        source = MIGRATION.read_text()
        match = re.search(r"def upgrade\(\).*?role IN \(([^)]*)\)", source, re.DOTALL)
        assert match, "ck_users_valid_role IN-list not found in migration"
        allowed = set(re.findall(r"'([a-z_]+)'", match.group(1)))
        assert UserRole.PLATFORM_OPERATOR.value in allowed
        # every enum member stays storable
        assert {r.value for r in UserRole} <= allowed


class TestEndToEnd:
    """Script-created operator through the real HTTP surface."""

    def _login(self, client, db):
        upsert_platform_operator(db, EMAIL, PASSWORD)
        db.commit()
        with patch("app.modules.auth.service.AuthService._send_otp_email") as send:
            resp = client.post(
                "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
            )
        assert resp.status_code == 200
        code = send.call_args[0][1]
        verify = client.post(
            "/api/v1/auth/verify-otp", json={"email": EMAIL, "code": code}
        )
        assert verify.status_code == 200
        data = verify.json()
        assert data["user"]["role"] == UserRole.PLATFORM_OPERATOR.value
        return {"Authorization": f"Bearer {data['access_token']}"}

    def test_operator_authenticates_and_lists_owners(self, client, db):
        headers = self._login(client, db)
        owner_id = OwnerService(db).get_or_create().id
        db.commit()

        resp = client.get("/api/v1/platform/owners", headers=headers)
        assert resp.status_code == 200
        assert [o["id"] for o in resp.json()["owners"]] == [str(owner_id)]

    def test_operator_still_rejected_by_tenant_gates(self, client, db):
        """Same isolation pins as test_module_entitlements: the operator is
        not a tenant role and gains no tenant data access (ADR-0011)."""
        headers = self._login(client, db)
        assert not UserRole.PLATFORM_OPERATOR.is_privileged
        assert client.get("/api/v1/owner/modules", headers=headers).status_code == 403
        assert client.get("/api/v1/reports/sales", headers=headers).status_code == 403


class TestCliSurface:
    def test_credentials_are_never_accepted_as_cli_args(self, capsys):
        for argv in (["--email", EMAIL], ["--password", PASSWORD], [EMAIL]):
            with pytest.raises(SystemExit) as excinfo:
                _parse_args(argv)
            assert excinfo.value.code != 0
        # the ONLY accepted option is the --force-role flag
        assert vars(_parse_args([])) == {"force_role": False}

    def test_missing_database_url_is_clear_nonzero_exit(self, tmp_path):
        """`python -m app.scripts.create_platform_operator` without a
        DATABASE_URL (env var or .env file — cwd is an empty tmp dir) must
        exit non-zero with a message naming DATABASE_URL, never a traceback
        dump ending in success."""
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        env["PYTHONPATH"] = str(API_DIR)
        env[ENV_EMAIL] = EMAIL
        env[ENV_PASSWORD] = PASSWORD
        result = subprocess.run(
            [sys.executable, "-m", "app.scripts.create_platform_operator"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode != 0
        assert "DATABASE_URL" in result.stderr
        # the password never appears in any output, success or failure
        assert PASSWORD not in result.stdout + result.stderr

    def test_main_success_output_never_contains_password(
        self, db, monkeypatch, capsys, caplog
    ):
        """Drive main() end-to-end against the test session: the password is
        sourced from the env, and only id + role + action are reported."""
        import app.common.database as database_module

        monkeypatch.setenv(ENV_EMAIL, EMAIL)
        monkeypatch.setenv(ENV_PASSWORD, PASSWORD)

        @contextlib.contextmanager
        def _test_session():
            yield db
            db.commit()

        monkeypatch.setattr(database_module, "get_db_context", _test_session)
        monkeypatch.setattr(
            database_module, "check_database_connection", lambda: True
        )

        with caplog.at_level("INFO"):
            main([])

        rows = _operator_rows(db)
        assert len(rows) == 1
        captured = capsys.readouterr()
        all_output = captured.out + captured.err + caplog.text
        assert PASSWORD not in all_output
        assert str(rows[0].id) in caplog.text
        assert UserRole.PLATFORM_OPERATOR.value in caplog.text
