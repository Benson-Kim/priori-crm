"""PostgreSQL trigger that makes ``audit_events`` append-only at the DB.

"Rows are never updated or deleted" (app/common/audit.py) was application
convention only: until the ``app_migrator``/``app_runtime`` role split
lands (#80), the single application role holds full UPDATE/DELETE on
``audit_events``. This BEFORE UPDATE OR DELETE trigger raises, turning the
append-only convention into a database control now (!77 review §4)
instead of waiting for the role split — and it stays on afterwards as
belt-and-braces (defense in depth against privilege drift).

The DDL is bound to the metadata ``after_create`` event so it is installed
by both ``Base.metadata.create_all`` (the Postgres-backed test path) and
by the Alembic migration (production), and is dropped on ``before_drop`` —
the same pattern as app/common/reference_triggers.py. PL/pgSQL is
Postgres-only by design; the SQLite fallback used for pure logic units
does not install or exercise it.

Deliberately row-level BEFORE, not a REVOKE: a REVOKE needs the role
split to be meaningful (the owner role bypasses its own REVOKE), whereas
a trigger binds every non-superuser path today. DBA maintenance that
legitimately must rewrite history (e.g. a court-ordered erasure) drops
the trigger explicitly in the same maintenance transaction — a loud,
auditable act rather than a silent UPDATE.
"""

from sqlalchemy import DDL, event

from app.common.database import Base

CREATE_AUDIT_APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION audit_events_block_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_events is append-only: % is forbidden (ADR-0013 / #71)',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_events_append_only
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
"""

DROP_AUDIT_APPEND_ONLY_SQL = """
DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events;
DROP FUNCTION IF EXISTS audit_events_block_mutation();
"""

# SQLAlchemy's DDL() applies ``statement % context`` to the whole text, so
# the literal percent sign in RAISE EXCEPTION must be doubled here. The
# canonical *_SQL strings above stay unescaped for Alembic's op.execute
# (which does not %-interpolate).
_create_ddl = DDL(CREATE_AUDIT_APPEND_ONLY_SQL.replace("%", "%%")).execute_if(
    dialect="postgresql"
)
_drop_ddl = DDL(DROP_AUDIT_APPEND_ONLY_SQL.replace("%", "%%")).execute_if(
    dialect="postgresql"
)

event.listen(Base.metadata, "after_create", _create_ddl)
event.listen(Base.metadata, "before_drop", _drop_ddl)
