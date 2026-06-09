"""PostgreSQL triggers that maintain the reference high-water mark (P-4 / V-DI-5).

The generator bumps ``reference_sequences`` when it issues a number, but numbers
can also enter the system by other paths (imports, data fixes, direct inserts).
To guarantee a suffix is *never* reused after the most-recent record is
hard-deleted, the high-water mark must advance for every issued reference
regardless of how the row was created. These AFTER INSERT triggers parse the
number/reference on each new row and upsert the scope counter to the greater of
its current value and the inserted suffix.

Scope keys mirror the generator's advisory-lock keys exactly:
- date-scoped numbers: ``"<lock_key>_<PREFIX-YYYYMMDD>"``
  e.g. ``invoice_number_INV-20260608``
- global references:   ``"<lock_key>"``
  e.g. ``invoice_reference_gen``

The DDL is bound to the metadata ``after_create`` event so it is installed by
both ``Base.metadata.create_all`` (the Postgres-backed test path) and by the
Alembic migration (production), and is dropped on ``before_drop``.

PL/pgSQL is Postgres-only by design: the reference-collision behaviour is only
meaningful on PostgreSQL (the CI service); the SQLite fallback used for pure
logic units does not install these triggers and does not exercise them.
"""

from sqlalchemy import DDL, event

from app.common.database import Base
from app.common.reference import DATE_SCOPED_REGEX, GLOBAL_REGEX

# A reusable PL/pgSQL function per (number kind). Each trigger passes the
# scope-key prefix (the generator's lock_key) and whether the column is
# date-scoped (PREFIX-YYYYMMDD-NNN) or global (PREFIX-NNNN) via TG_ARGV.
_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION bump_reference_sequence()
RETURNS trigger AS $$
DECLARE
    v_col_value   text;
    v_lock_key    text := TG_ARGV[0];
    v_date_scoped boolean := TG_ARGV[1]::boolean;
    v_scope_key   text;
    v_suffix      bigint;
BEGIN
    -- TG_ARGV[2] is the column name holding the reference string. Read it
    -- dynamically via row_to_json so no SQL identifier-format specifier is
    -- needed (the SQLAlchemy DDL wrapper would misread a literal percent sign
    -- as a Python format placeholder).
    --
    -- Locals are v_-prefixed so they cannot collide with the
    -- reference_sequences column names (scope_key/last_value) in the
    -- INSERT ... ON CONFLICT below, which would otherwise raise
    -- AmbiguousColumn.
    v_col_value := row_to_json(NEW) ->> TG_ARGV[2];
    IF v_col_value IS NULL THEN
        RETURN NEW;
    END IF;

    IF v_date_scoped THEN
        -- PREFIX-YYYYMMDD-NNN : scope is lock_key + 'PREFIX-YYYYMMDD'.
        IF v_col_value !~ '{DATE_SCOPED_REGEX}' THEN
            RETURN NEW;
        END IF;
        v_scope_key := v_lock_key || '_' || split_part(v_col_value, '-', 1)
                       || '-' || split_part(v_col_value, '-', 2);
        v_suffix := split_part(v_col_value, '-', 3)::bigint;
    ELSE
        -- PREFIX-NNNN : scope is the bare lock_key.
        IF v_col_value !~ '{GLOBAL_REGEX}' THEN
            RETURN NEW;
        END IF;
        v_scope_key := v_lock_key;
        v_suffix := split_part(v_col_value, '-', 2)::bigint;
    END IF;

    INSERT INTO reference_sequences (scope_key, last_value)
    VALUES (v_scope_key, v_suffix)
    ON CONFLICT (scope_key)
    DO UPDATE SET last_value = GREATEST(reference_sequences.last_value, EXCLUDED.last_value);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# (trigger name, table, lock_key, date_scoped, column)
_TRIGGERS = (
    ("trg_bump_invoice_number", "invoices", "invoice_number", "true", "invoice_number"),
    (
        "trg_bump_invoice_reference",
        "invoices",
        "invoice_reference_gen",
        "false",
        "invoice_reference",
    ),
    ("trg_bump_quote_number", "quotes", "quote_number", "true", "quote_number"),
    (
        "trg_bump_quote_reference",
        "quotes",
        "quote_reference_gen",
        "false",
        "quote_reference",
    ),
    ("trg_bump_expense_number", "expenses", "expense_number", "true", "expense_number"),
    (
        "trg_bump_expense_reference",
        "expenses",
        "expense_reference_gen",
        "false",
        "expense_reference",
    ),
)


def _create_sql() -> str:
    parts = [_FUNCTION_SQL]
    for name, table, lock_key, date_scoped, column in _TRIGGERS:
        parts.append(
            f"CREATE TRIGGER {name} AFTER INSERT ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION "
            f"bump_reference_sequence('{lock_key}', '{date_scoped}', '{column}');"
        )
    return "\n".join(parts)


def _drop_sql() -> str:
    parts = [
        f"DROP TRIGGER IF EXISTS {name} ON {table};" for name, table, *_ in _TRIGGERS
    ]
    parts.append("DROP FUNCTION IF EXISTS bump_reference_sequence();")
    return "\n".join(parts)


CREATE_REFERENCE_TRIGGERS_SQL = _create_sql()
DROP_REFERENCE_TRIGGERS_SQL = _drop_sql()

# Install only on PostgreSQL; bound after all tables (incl. reference_sequences,
# invoices, quotes, expenses) are created.
#
# SQLAlchemy's DDL() applies ``statement % context`` to the whole text, so any
# literal percent sign must be doubled. The canonical *_SQL strings above are
# left unescaped for Alembic's op.execute (which does not %-interpolate).
_create_ddl = DDL(CREATE_REFERENCE_TRIGGERS_SQL.replace("%", "%%")).execute_if(
    dialect="postgresql"
)
_drop_ddl = DDL(DROP_REFERENCE_TRIGGERS_SQL.replace("%", "%%")).execute_if(
    dialect="postgresql"
)

event.listen(Base.metadata, "after_create", _create_ddl)
event.listen(Base.metadata, "before_drop", _drop_ddl)
