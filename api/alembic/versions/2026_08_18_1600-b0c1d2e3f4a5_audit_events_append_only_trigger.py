"""Make audit_events append-only with a BEFORE UPDATE OR DELETE trigger

Revision ID: b0c1d2e3f4a5
Revises: f3a4b5c6d7e8
Create Date: 2026-08-18 16:00:00.000000

Issue #71 / !77 review §4: "rows are never updated or deleted" was
application convention only — until the app_migrator/app_runtime role
split (#80) the single application role holds full UPDATE/DELETE on
``audit_events``. This trigger raises on any UPDATE or DELETE, turning
the append-only convention into a database control now, four phases
early. It stays on after the role split as belt-and-braces.

The DDL is shared with app/common/audit_triggers.py (which installs the
same trigger on the Postgres-backed test path via metadata events), so
production and tests can never drift.

Chained onto f3a4b5c6d7e8 (add_owner_profile_status), the single head at
the time of writing (verified by walking down_revision links).
"""

from typing import Sequence, Union

from alembic import op

from app.common.audit_triggers import (
    CREATE_AUDIT_APPEND_ONLY_SQL,
    DROP_AUDIT_APPEND_ONLY_SQL,
)

# revision identifiers, used by Alembic.
revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(CREATE_AUDIT_APPEND_ONLY_SQL)


def downgrade() -> None:
    op.execute(DROP_AUDIT_APPEND_ONLY_SQL)
