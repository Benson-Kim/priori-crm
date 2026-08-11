"""Add dual USD/KES customer billing profiles + customer desk fields

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-09 10:00:00.000000

Deal-desk parity for the customers module (issue #43):

- customers gain nullable industry (the 12 values ratified from the Sales
  Desk designs — see issue #53), tenant_domain, owner_id (FK users, SET NULL)
  and primary_currency (USD|KES).
- New customer_billing_profiles table: exactly one profile per customer per
  billing currency (UNIQUE(customer_id, currency)), with a globally unique
  code (name slug + monotonic reference_sequences suffix — NOT the
  prototype's collision-prone bare 3-letter slug), payment terms, tax
  treatment (display values mapping 1:1 onto TaxType), credit_limit stored
  as Numeric in the profile's OWN currency (no floats, no USD round-trip),
  and the synced/synced_at posting flag (see ADR 0010).
- Backfills BOTH profiles for every existing customer from the seed defaults
  in app.constants.settings_defaults (USD: 30 days / Zero-rated (export) /
  25000 · KES: 14 days / VAT 16% / 10000) and advances the
  reference_sequences high-water mark past every suffix issued, so the live
  generator can never re-issue a backfilled code.

The backfill logic is shared with the runtime create path via
app.modules.customers.profile_backfill (precedent: the reference-trigger
migration imports app.common.reference_triggers), which pins the defaults
and code format to a single source of truth.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.modules.customers.profile_backfill import (
    PROFILE_CODE_SCOPE_KEY,
    backfill_billing_profiles,
)

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Ratified industry vocabulary (docs/sales-desk-designs, issue #53).
# Must stay identical to app.constants.enums.Industry — kept as a literal
# tuple (not derived from the enum) so this migration's CHECK stays frozen
# at the ratified list; a future vocabulary change gets its own migration.
_INDUSTRY_VALUES = (
    "Financial services",
    "Healthcare",
    "Education",
    "Logistics & transport",
    "Hospitality & tourism",
    "Agriculture & export",
    "Insurance",
    "Manufacturing",
    "Professional services",
    "NGO / Non-profit",
    "Retail",
    "Other",
)
_INDUSTRY_IN_LIST = ", ".join(f"'{v}'" for v in _INDUSTRY_VALUES)
_TAX_TREATMENT_IN_LIST = "'VAT 16%', 'Zero-rated (export)', 'Exempt'"
# Mirrors app.constants.enums.BillingPaymentTerms (symmetry with the
# tax_treatment CHECK — !38 review, non-blocking item 5).
_PAYMENT_TERMS_IN_LIST = "'On receipt', '14 days', '30 days', '45 days', '60 days'"


def upgrade() -> None:
    # --- customers: desk fields -------------------------------------------
    op.add_column(
        "customers",
        sa.Column(
            "industry",
            sa.String(length=50),
            nullable=True,
            comment="Industry classification (ratified design vocabulary, #53)",
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "tenant_domain",
            sa.String(length=255),
            nullable=True,
            comment="Microsoft 365 tenant domain (e.g. acme.onmicrosoft.com)",
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="Account owner (sales rep) responsible for this customer",
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "primary_currency",
            sa.String(length=3),
            nullable=True,
            comment="Which billing profile (USD|KES) is the customer's default",
        ),
    )
    op.create_foreign_key(
        "fk_customers_owner_id_users",
        "customers",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_customers_owner_id", "customers", ["owner_id"])
    op.create_check_constraint(
        "ck_customers_valid_industry",
        "customers",
        f"industry IS NULL OR industry IN ({_INDUSTRY_IN_LIST})",
    )
    op.create_check_constraint(
        "ck_customers_valid_primary_currency",
        "customers",
        "primary_currency IS NULL OR primary_currency IN ('USD', 'KES')",
    )

    # --- customer_billing_profiles ----------------------------------------
    op.create_table(
        "customer_billing_profiles",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            comment="Billing currency of this profile (USD or KES)",
        ),
        sa.Column(
            "code",
            sa.String(length=30),
            nullable=False,
            comment=(
                "Accounting profile code: name slug + monotonic reference-"
                "sequence suffix + currency (e.g. BAR-0007-USD)"
            ),
        ),
        sa.Column(
            "payment_terms",
            sa.String(length=20),
            nullable=False,
            comment="Payment terms label (e.g. 'On receipt', '30 days')",
        ),
        sa.Column(
            "tax_treatment",
            sa.String(length=30),
            nullable=False,
            comment="Tax treatment display value; maps 1:1 onto TaxType",
        ),
        sa.Column(
            "credit_limit",
            sa.Numeric(precision=15, scale=2),
            nullable=False,
            comment="Credit limit expressed in the profile's own currency",
        ),
        sa.Column(
            "synced",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Whether the profile has been pushed to accounting",
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the profile was last pushed to accounting",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Optimistic-locking version counter",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_customer_billing_profiles_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer_billing_profiles"),
        sa.UniqueConstraint(
            "customer_id",
            "currency",
            name="uq_customer_billing_profiles_customer_id_currency",
        ),
        sa.UniqueConstraint("code", name="uq_customer_billing_profiles_code"),
        sa.CheckConstraint(
            "currency IN ('USD', 'KES')",
            name="ck_customer_billing_profiles_valid_currency",
        ),
        sa.CheckConstraint(
            f"tax_treatment IN ({_TAX_TREATMENT_IN_LIST})",
            name="ck_customer_billing_profiles_valid_tax_treatment",
        ),
        sa.CheckConstraint(
            f"payment_terms IN ({_PAYMENT_TERMS_IN_LIST})",
            name="ck_customer_billing_profiles_valid_payment_terms",
        ),
        sa.CheckConstraint(
            "credit_limit >= 0",
            name="ck_customer_billing_profiles_credit_limit_non_negative",
        ),
    )
    op.create_index(
        "ix_customer_billing_profiles_customer_id",
        "customer_billing_profiles",
        ["customer_id"],
    )
    # Partial index serving the ?unsynced=true EXISTS probe: the interesting
    # rows are the (few) unsynced ones, so index their customer_id directly
    # instead of a low-selectivity boolean index over every row (!38 review,
    # non-blocking item 4).
    op.create_index(
        "ix_customer_billing_profiles_unsynced_customer",
        "customer_billing_profiles",
        ["customer_id"],
        postgresql_where=sa.text("NOT synced"),
    )

    # --- backfill: both profiles for every existing customer ---------------
    backfill_billing_profiles(op.get_bind())


def downgrade() -> None:
    op.drop_index(
        "ix_customer_billing_profiles_unsynced_customer",
        table_name="customer_billing_profiles",
    )
    op.drop_index(
        "ix_customer_billing_profiles_customer_id",
        table_name="customer_billing_profiles",
    )
    op.drop_table("customer_billing_profiles")
    # The feature (and its whole numbering scope) is being removed, so the
    # high-water row goes with it; a later re-upgrade starts a fresh scope
    # against an empty table.
    op.execute(
        sa.text("DELETE FROM reference_sequences WHERE scope_key = :key").bindparams(
            key=PROFILE_CODE_SCOPE_KEY
        )
    )
    op.drop_constraint(
        "ck_customers_valid_primary_currency", "customers", type_="check"
    )
    op.drop_constraint("ck_customers_valid_industry", "customers", type_="check")
    op.drop_index("ix_customers_owner_id", table_name="customers")
    op.drop_constraint("fk_customers_owner_id_users", "customers", type_="foreignkey")
    op.drop_column("customers", "primary_currency")
    op.drop_column("customers", "owner_id")
    op.drop_column("customers", "tenant_domain")
    op.drop_column("customers", "industry")
