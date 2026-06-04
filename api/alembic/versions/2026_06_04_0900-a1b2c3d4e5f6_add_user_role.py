"""add user role column

Revision ID: a1b2c3d4e5f6
Revises: 683d60a50164
Create Date: 2026-06-04 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '683d60a50164'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'role',
            sa.String(length=20),
            nullable=False,
            server_default='member',
        ),
    )
    op.create_check_constraint(
        'ck_users_valid_role',
        'users',
        "role IN ('admin', 'manager', 'member')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_users_valid_role', 'users', type_='check')
    op.drop_column('users', 'role')
