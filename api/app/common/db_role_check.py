"""Startup ownership-drift check for the app_migrator/app_runtime role split.

ADR-0013 (Phase T1, pulled forward per the !77 reviews; rollout tracked in
issue #80) splits database access into ``app_migrator`` — owns every
application table, the only role Alembic runs as — and ``app_runtime`` —
the API's connection role, which must own nothing. Row-Level Security
policies never bind a table's *owner* unless ``FORCE ROW LEVEL SECURITY``
is set, so a runtime role that quietly owns (or regains) tables would turn
the per-wave RLS backstop (T2-T4) into a no-op for exactly the connections
that matter. This check makes that drift a loud startup failure instead of
a silent security regression.

It only enforces when the split is **declared active**, so existing
single-role deployments keep working unchanged until the DBA executes the
split (issue #80's rollout checklist):

- explicitly, via ``DB_ROLE_SPLIT_ACTIVE=true`` in the environment, or
- implicitly, when the API's connection role *is* the runtime role name —
  connecting as ``app_runtime`` means the DSN cutover happened, whether or
  not the flag was remembered.

The query mirrors the canonical verification in
``docs/operations/sql/create-db-roles.sql`` §5 (``pg_tables`` ownership;
``REASSIGN OWNED`` also transfers sequences/views, so table ownership is
the drift sentinel).
"""

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.lib.config import settings

logger = logging.getLogger(__name__)

# The runtime role name from docs/operations/sql/create-db-roles.sql.
RUNTIME_ROLE_NAME = "app_runtime"

_OWNED_TABLES_SQL = text(
    "SELECT tablename FROM pg_tables "
    "WHERE schemaname = 'public' AND tableowner = current_user "
    "ORDER BY tablename"
)


class OwnershipDriftError(RuntimeError):
    """The runtime role owns application tables while the split is active."""


def verify_runtime_role_ownership(engine: Engine) -> None:
    """Fail closed at startup on runtime-role table ownership drift.

    No-op unless the role split is declared active (flag or role-name
    detection, see module docstring). Raises :class:`OwnershipDriftError`
    when the connection role owns any table in ``public`` — the deploy's
    health check then fails and the release rolls back instead of running
    a wave of tenant keys without a working RLS backstop.
    """
    if engine.dialect.name != "postgresql":
        # SQLite dev/test runs have no roles or ownership to verify.
        if settings.DB_ROLE_SPLIT_ACTIVE:
            logger.warning(
                "DB_ROLE_SPLIT_ACTIVE is set but the database dialect is %r, "
                "not PostgreSQL — ownership drift check skipped",
                engine.dialect.name,
            )
        return

    with engine.connect() as conn:
        current_role = conn.execute(text("SELECT current_user")).scalar_one()
        active = settings.DB_ROLE_SPLIT_ACTIVE or current_role == RUNTIME_ROLE_NAME
        if not active:
            logger.info(
                "app_migrator/app_runtime role split not declared active "
                "(connected as %r, DB_ROLE_SPLIT_ACTIVE=false) — ownership "
                "drift check skipped; single-role deployment assumed (#80)",
                current_role,
            )
            return
        owned = conn.execute(_OWNED_TABLES_SQL).scalars().all()

    if owned:
        preview = ", ".join(owned[:10]) + (" …" if len(owned) > 10 else "")
        logger.critical(
            "OWNERSHIP DRIFT: the role split is active but connection role "
            "%r owns %d application table(s): %s. Refusing to start — an "
            "owning runtime role silently disables the FORCE RLS backstop "
            "(ADR-0013). Run `REASSIGN OWNED BY %s TO app_migrator;` per "
            "docs/operations/sql/create-db-roles.sql and issue #80.",
            current_role,
            len(owned),
            preview,
            current_role,
        )
        raise OwnershipDriftError(
            f"role split active but {current_role!r} owns {len(owned)} "
            f"application table(s) ({preview}); the runtime role must own "
            "nothing — see docs/operations/sql/create-db-roles.sql and "
            "issue #80"
        )

    logger.info(
        "role split active: connection role %r owns no application tables — "
        "ownership drift check passed",
        current_role,
    )
