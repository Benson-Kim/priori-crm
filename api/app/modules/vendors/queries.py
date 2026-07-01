"""Vendor read-side query objects for the Supplier Statements cards (PO-33).

Decomposes the card data-access out of VendorService so the list endpoints and
the per-card exports share one source of truth for filtering and the derived
PO-status bucketing (paid / pending / overpaid). All queries are vendor-scoped,
read-only (never flush/commit), and lean on the existing indexes
(``purchase_orders(vendor_id, status)``, ``purchase_order_payments(po_id)``,
expenses by vendor).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.constants.enums import PurchaseOrderStatus
from app.modules.vendors.schemas import (
    VendorInvoiceRow,
    VendorInvoicesCard,
    VendorPaymentRow,
    VendorPaymentsCard,
    VendorPurchaseOrderRow,
    VendorPurchaseOrdersCard,
)

# The three display buckets for a non-DRAFT PO. There is deliberately no
# 'overdue' bucket: a purchase order carries no payment due date, so overdue
# is not a meaningful PO state (product decision, PO-33).
PoDerivedStatus = Literal["paid", "pending", "overpaid"]


def derive_po_status(status: str, balance_due: Decimal) -> PoDerivedStatus:
    """Map a non-DRAFT PO to its Supplier-Statements display bucket.

    - paid     : settled exactly (status PAID, balance_due == 0)
    - overpaid : settled beyond total (status PAID, balance_due < 0)
    - pending  : sent but not settled (status SENT, any open balance)

    The caller must have already excluded DRAFT POs. This is the single source
    of truth for the bucketing so the list, the summary counts and the export
    can never drift.
    """
    if status == PurchaseOrderStatus.PAID:
        return "overpaid" if balance_due < Decimal("0.00") else "paid"
    # Everything else reaching here is a SENT PO with an open (or zero-but-not
    # -PAID) balance.
    return "pending"


class VendorCardsRepository:
    """Read-side data access for the vendor Supplier Statements cards."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def purchase_orders(
        self,
        vendor_id: uuid.UUID,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> VendorPurchaseOrdersCard:
        """Non-DRAFT POs for the vendor plus paid/pending/overpaid counts.

        Filtered by ``order_date`` within the optional range. DRAFT POs are
        excluded from both the row list and the summary counts.
        """
        from app.modules.purchase_orders.models import PurchaseOrder

        query = self._db.query(
            PurchaseOrder.id,
            PurchaseOrder.po_reference,
            PurchaseOrder.order_date,
            PurchaseOrder.total,
            PurchaseOrder.balance_due,
            PurchaseOrder.status,
            PurchaseOrder.currency,
        ).filter(
            PurchaseOrder.vendor_id == vendor_id,
            PurchaseOrder.status != PurchaseOrderStatus.DRAFT,
        )
        if date_from:
            query = query.filter(PurchaseOrder.order_date >= date_from)
        if date_to:
            query = query.filter(PurchaseOrder.order_date <= date_to)

        rows: list[VendorPurchaseOrderRow] = []
        counts = {"paid": 0, "pending": 0, "overpaid": 0}
        for row in query.order_by(PurchaseOrder.order_date.desc()).all():
            derived = derive_po_status(row.status, row.balance_due)
            counts[derived] += 1
            rows.append(
                VendorPurchaseOrderRow(
                    id=row.id,
                    po_reference=row.po_reference,
                    order_date=row.order_date,
                    amount=row.total,
                    currency=row.currency,
                    derived_status=derived,
                )
            )

        return VendorPurchaseOrdersCard(
            rows=rows,
            paid=counts["paid"],
            pending=counts["pending"],
            overpaid=counts["overpaid"],
        )

    def po_payments(
        self,
        vendor_id: uuid.UUID,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> VendorPaymentsCard:
        """The vendor's PO payments (joined to their PO), date-filtered.

        One query joining payments to their parent PO scoped to the vendor;
        document presence is a correlated EXISTS so the row cost stays
        constant (no per-payment document fetch).
        """
        from sqlalchemy import exists

        from app.modules.purchase_orders.models import (
            PurchaseOrder,
            PurchaseOrderDocument,
            PurchaseOrderPayment,
        )

        has_doc = (
            exists()
            .where(PurchaseOrderDocument.payment_id == PurchaseOrderPayment.id)
            .label("has_document")
        )

        query = (
            self._db.query(
                PurchaseOrderPayment.id,
                PurchaseOrder.po_reference,
                PurchaseOrderPayment.payment_date,
                PurchaseOrderPayment.invoice_number,
                PurchaseOrderPayment.reference,
                PurchaseOrderPayment.amount,
                PurchaseOrderPayment.currency,
                has_doc,
            )
            .join(PurchaseOrder, PurchaseOrderPayment.po_id == PurchaseOrder.id)
            .filter(PurchaseOrder.vendor_id == vendor_id)
        )
        if date_from:
            query = query.filter(PurchaseOrderPayment.payment_date >= date_from)
        if date_to:
            query = query.filter(PurchaseOrderPayment.payment_date <= date_to)

        rows: list[VendorPaymentRow] = []
        total = Decimal("0.00")
        for row in query.order_by(PurchaseOrderPayment.payment_date.desc()).all():
            rows.append(
                VendorPaymentRow(
                    id=row.id,
                    po_reference=row.po_reference,
                    payment_date=row.payment_date,
                    invoice_number=row.invoice_number,
                    reference=row.reference,
                    amount=row.amount,
                    currency=row.currency,
                    has_document=bool(row.has_document),
                )
            )
            total += row.amount

        return VendorPaymentsCard(rows=rows, total_amount=total)

    def invoices(
        self,
        vendor_id: uuid.UUID,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> VendorInvoicesCard:
        """The vendor's invoices/bills (from the expenses ledger), date-filtered."""
        from app.modules.expenses.models import Expense

        query = self._db.query(
            Expense.id,
            Expense.expense_number,
            Expense.expense_date,
            Expense.total_due,
            Expense.balance_due,
            Expense.status,
            Expense.currency,
        ).filter(Expense.vendor_id == vendor_id)
        if date_from:
            query = query.filter(Expense.expense_date >= date_from)
        if date_to:
            query = query.filter(Expense.expense_date <= date_to)

        rows: list[VendorInvoiceRow] = []
        total = Decimal("0.00")
        for row in query.order_by(Expense.expense_date.desc()).all():
            rows.append(
                VendorInvoiceRow(
                    id=row.id,
                    ref_no=row.expense_number,
                    invoice_date=row.expense_date,
                    amount=row.total_due,
                    balance=row.balance_due,
                    status=row.status,
                    currency=row.currency,
                )
            )
            total += row.total_due

        return VendorInvoicesCard(rows=rows, total_amount=total)
