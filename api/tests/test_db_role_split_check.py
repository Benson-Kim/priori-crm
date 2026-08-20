"""Startup ownership-drift check for the role split (ADR-0013 T1, issue #80).

Covers BOTH modes of ``verify_runtime_role_ownership``:

- split NOT declared active → the check is a no-op even when the connection
  role owns every table (i.e. today's single-role deployments keep booting
  unchanged, before the DBA executes the split);
- split active (env flag OR app_runtime role-name detection) → fails closed
  on ownership drift, passes when the role owns nothing.

The unit legs drive the function through a stub engine so the matrix runs
on every backend; the integration legs exercise the real query against
PostgreSQL when the suite has one (CI's api:test job).
"""

from unittest.mock import MagicMock

import pytest

from app.common.db_role_check import (
    RUNTIME_ROLE_NAME,
    OwnershipDriftError,
    verify_runtime_role_ownership,
)
from app.lib.config import settings
from tests.conftest import USING_POSTGRES
from tests.conftest import engine as test_engine


def _stub_engine(
    dialect: str = "postgresql",
    current_user: str = "priori",
    owned: tuple[str, ...] = (),
) -> MagicMock:
    """Engine double: answers the current_user and ownership queries."""
    engine = MagicMock()
    engine.dialect.name = dialect
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    def _execute(stmt, *args, **kwargs):
        result = MagicMock()
        if "pg_tables" in str(stmt):
            result.scalars.return_value.all.return_value = list(owned)
        else:
            result.scalar_one.return_value = current_user
        return result

    conn.execute.side_effect = _execute
    return engine


@pytest.mark.no_db
class TestSplitNotActive:
    """Single-role deployments must keep working unchanged."""

    def test_skips_even_when_role_owns_tables(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", False)
        engine = _stub_engine(current_user="priori", owned=("customers", "invoices"))
        verify_runtime_role_ownership(engine)  # must not raise

    def test_ownership_query_never_runs_when_inactive(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", False)
        engine = _stub_engine(current_user="priori", owned=("customers",))
        verify_runtime_role_ownership(engine)
        conn = engine.connect.return_value.__enter__.return_value
        executed = [str(call.args[0]) for call in conn.execute.call_args_list]
        assert not any("pg_tables" in sql for sql in executed)

    def test_non_postgres_dialect_skips_without_connecting(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", False)
        engine = _stub_engine(dialect="sqlite")
        verify_runtime_role_ownership(engine)
        engine.connect.assert_not_called()

    def test_non_postgres_dialect_skips_even_with_flag(self, monkeypatch):
        # Misconfiguration (flag on, SQLite dev DB) must warn, not crash.
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", True)
        engine = _stub_engine(dialect="sqlite")
        verify_runtime_role_ownership(engine)
        engine.connect.assert_not_called()


@pytest.mark.no_db
class TestSplitActive:
    """Once declared active, ownership drift must abort startup."""

    def test_flag_with_drift_fails_closed(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", True)
        engine = _stub_engine(current_user="app_runtime", owned=("customers",))
        with pytest.raises(OwnershipDriftError) as exc:
            verify_runtime_role_ownership(engine)
        assert "app_runtime" in str(exc.value)
        assert "customers" in str(exc.value)

    def test_flag_with_clean_ownership_passes(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", True)
        engine = _stub_engine(current_user="app_runtime", owned=())
        verify_runtime_role_ownership(engine)  # must not raise

    def test_role_name_detection_without_flag(self, monkeypatch):
        # Connecting AS app_runtime is the split, even if the flag was
        # forgotten during the DSN cutover.
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", False)
        engine = _stub_engine(current_user=RUNTIME_ROLE_NAME, owned=("audit_events",))
        with pytest.raises(OwnershipDriftError):
            verify_runtime_role_ownership(engine)

    def test_role_name_detection_clean_passes(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", False)
        engine = _stub_engine(current_user=RUNTIME_ROLE_NAME, owned=())
        verify_runtime_role_ownership(engine)  # must not raise

    def test_flag_with_non_runtime_role_and_drift_fails(self, monkeypatch):
        # The flag alone activates enforcement regardless of the role name:
        # a half-executed cutover (flag flipped, DSN still the old role)
        # must fail loudly, not silently keep the owning role in traffic.
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", True)
        engine = _stub_engine(current_user="priori", owned=("invoices",))
        with pytest.raises(OwnershipDriftError):
            verify_runtime_role_ownership(engine)


@pytest.mark.skipif(not USING_POSTGRES, reason="requires PostgreSQL")
class TestAgainstRealPostgres:
    """Real-query legs against the CI database.

    The suite's ``setup_db`` fixture creates every table as the connecting
    role, so that role OWNS them by construction — real drift, real query.
    """

    def test_inactive_split_boots_despite_ownership(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", False)
        verify_runtime_role_ownership(test_engine)  # must not raise

    def test_active_split_detects_real_drift(self, monkeypatch):
        monkeypatch.setattr(settings, "DB_ROLE_SPLIT_ACTIVE", True)
        with pytest.raises(OwnershipDriftError):
            verify_runtime_role_ownership(test_engine)
