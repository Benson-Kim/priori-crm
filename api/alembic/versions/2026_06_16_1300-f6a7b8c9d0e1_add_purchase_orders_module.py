"""add purchase orders module

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-16 13:00:00.000000

Creates the Purchase Orders module tables (PO-01):
- purchase_orders
- purchase_order_line_items
- purchase_order_documents

Mirrors the expenses module schema (vendor-facing). No discount columns,
no payments table in v1.

converted_bill_id is a plain nullable UUID column with NO foreign-key
constraint because the Bills module/table does not exist yet (forward-
tolerant per PO-01; the FK is added in a later migration once Bills lands).
owner_snapshot_id references the existing owner_profile_snapshots table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("po_number", sa.String(length=50), nullable=False),
        sa.Column("po_reference", sa.String(length=50), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column("compliance_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'KES'"),
            nullable=False,
        ),
        sa.Column(
            "is_recurring",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "subtotal",
            sa.Numeric(precision=15, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "tax_total",
            sa.Numeric(precision=15, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "total",
            sa.Numeric(precision=15, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("terms_and_conditions", sa.Text(), nullable=True),
        sa.Column("converted_bill_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'billed', 'cancelled')",
            name="ck_purchase_orders_valid_status",
        ),
        sa.CheckConstraint(
            "delivery_date IS NULL OR delivery_date >= order_date",
            name="ck_purchase_orders_delivery_after_order_date",
        ),
        sa.CheckConstraint(
            "subtotal >= 0", name="ck_purchase_orders_subtotal_non_negative"
        ),
        sa.CheckConstraint(
            "tax_total >= 0", name="ck_purchase_orders_tax_total_non_negative"
        ),
        sa.CheckConstraint(
            "total >= 0", name="ck_purchase_orders_total_non_negative"
        ),
        sa.CheckConstraint(
            "currency IN ('KES', 'USD', 'EUR', 'GBP')",
            name="ck_purchase_orders_valid_currency",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name="fk_purchase_orders_vendor_id_vendors",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_snapshot_id"],
            ["owner_profile_snapshots.id"],
            name="fk_purchase_orders_owner_snapshot_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_orders"),
        sa.UniqueConstraint("po_number", name="uq_purchase_orders_po_number"),
        sa.UniqueConstraint("po_reference", name="uq_purchase_orders_po_reference"),
    )
    op.create_index(
        op.f("ix_purchase_orders_po_number"),
        "purchase_orders",
        ["po_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_purchase_orders_po_reference"),
        "purchase_orders",
        ["po_reference"],
        unique=True,
    )
    op.create_index(
        op.f("ix_purchase_orders_vendor_id"),
        "purchase_orders",
        ["vendor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_orders_status"),
        "purchase_orders",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_orders_converted_bill_id"),
        "purchase_orders",
        ["converted_bill_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_orders_vendor_status",
        "purchase_orders",
        ["vendor_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_orders_status_delivery_date",
        "purchase_orders",
        ["status", "delivery_date"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_orders_order_date",
        "purchase_orders",
        ["order_date"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_orders_created_at",
        "purchase_orders",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "purchase_order_line_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("tax_type", sa.String(length=20), nullable=False),
        sa.Column(
            "tax_amount",
            sa.Numeric(precision=15, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
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
        sa.CheckConstraint(
            "quantity > 0", name="ck_po_line_items_quantity_positive"
        ),
        sa.CheckConstraint(
            "unit_price >= 0", name="ck_po_line_items_price_non_negative"
        ),
        sa.CheckConstraint(
            "line_total >= 0", name="ck_po_line_items_total_non_negative"
        ),
        sa.CheckConstraint(
            "tax_amount >= 0", name="ck_po_line_items_tax_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["po_id"],
            ["purchase_orders.id"],
            name="fk_po_line_items_po_id_purchase_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order_line_items"),
        sa.UniqueConstraint(
            "po_id", "line_number", name="uq_po_line_items_po_line_number"
        ),
    )
    op.create_index(
        op.f("ix_purchase_order_line_items_po_id"),
        "purchase_order_line_items",
        ["po_id"],
        unique=False,
    )

    op.create_table(
        "purchase_order_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("storage_key", sa.String(length=1000), nullable=False),
        sa.Column(
            "source",
            sa.String(length=20),
            server_default=sa.text("'form'"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "source IN ('form', 'view')",
            name="ck_po_documents_valid_source",
        ),
        sa.CheckConstraint(
            "file_size_bytes > 0",
            name="ck_po_documents_size_positive",
        ),
        sa.ForeignKeyConstraint(
            ["po_id"],
            ["purchase_orders.id"],
            name="fk_po_documents_po_id_purchase_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order_documents"),
    )
    op.create_index(
        "ix_purchase_order_documents_po_id",
        "purchase_order_documents",
        ["po_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_purchase_order_documents_po_id",
        table_name="purchase_order_documents",
    )
    op.drop_table("purchase_order_documents")

    op.drop_index(
        op.f("ix_purchase_order_line_items_po_id"),
        table_name="purchase_order_line_items",
    )
    op.drop_table("purchase_order_line_items")

    op.drop_index("ix_purchase_orders_created_at", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_order_date", table_name="purchase_orders")
    op.drop_index(
        "ix_purchase_orders_status_delivery_date", table_name="purchase_orders"
    )
    op.drop_index(
        "ix_purchase_orders_vendor_status", table_name="purchase_orders"
    )
    op.drop_index(
        op.f("ix_purchase_orders_converted_bill_id"),
        table_name="purchase_orders",
    )
    op.drop_index(op.f("ix_purchase_orders_status"), table_name="purchase_orders")
    op.drop_index(
        op.f("ix_purchase_orders_vendor_id"), table_name="purchase_orders"
    )
    op.drop_index(
        op.f("ix_purchase_orders_po_reference"), table_name="purchase_orders"
    )
    op.drop_index(
        op.f("ix_purchase_orders_po_number"), table_name="purchase_orders"
    )
    op.drop_table("purchase_orders")
