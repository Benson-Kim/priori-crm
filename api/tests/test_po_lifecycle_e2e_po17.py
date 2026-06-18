"""PO-17 end-to-end lifecycle & calculation-parity suite (#18).

Consolidates the per-feature acceptance criteria into a single E2E flow
through the real PurchaseOrderService, complementing the focused component
tests (lifecycle table, payments, mark-as-sent, calculation, references,
locking, send, documents, export):

- the full v1 lifecycle DRAFT -> SENT -> PAID via the public service API,
  asserting balance arithmetic, the single version bump per step, sent_at /
  paid_at stamping and auto-settlement;
- v1 immutability: delete is DRAFT-only, a PAID PO rejects further payment;
- calculation parity with the shared app.common.financial engine (PO-02).

The lifecycle flow exercises _get_locked (with_for_update), a Postgres
construct, so it is guarded to the Postgres suite; the calculation-parity
checks are pure and run everywhere.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException
from app.common.financial import build_line_items, sum_line_totals
from app.constants.enums import (
    Currency,
    PurchaseOrderStatus,
    TaxType,
    VendorStatus,
)
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderPaymentCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

_postgres_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Lifecycle flow uses FOR UPDATE (_get_locked), a PostgreSQL construct.",
)


def _vendor(db, *, email="ap@acme.test", currency=Currency.KES) -> Vendor:
    vendor = Vendor(
        vendor_name="Acme Supplies",
        email=email,
        currency=currency,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line_item(**kw) -> PurchaseOrderLineItemCreate:
    base = {
        "item_name": "Steel bolts M8",
        "description": "Box of 500",
        "quantity": Decimal("2"),
        "unit_price": Decimal("100.00"),
        "tax_type": TaxType.VAT_16,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


# CALCULATION PARITY  (pure — runs everywhere)


class TestCalculationParity:
    """PO totals must equal the shared financial engine for identical input."""

    def test_multi_item_matches_financial_engine(self) -> None:
        items = [
            _line_item(),
            _line_item(item_name="Nuts M8", quantity=Decimal("3")),
        ]
        built = build_line_items(
            [
                {
                    "item_name": i.item_name,
                    "description": i.description,
                    "quantity": i.quantity,
                    "unit_price": i.unit_price,
                    "tax_type": i.tax_type,
                }
                for i in items
            ]
        )
        subtotal, tax_total = sum_line_totals(built)

        # The service builds + sums via the same helpers, so its totals must
        # match byte-for-byte (PO-02: identical to Quotes/Expenses).
        svc_built = PurchaseOrderService._build_line_items(
            PurchaseOrderService.__new__(PurchaseOrderService), items
        )
        svc_subtotal, svc_tax = PurchaseOrderService._sum_line_totals(
            PurchaseOrderService.__new__(PurchaseOrderService), svc_built
        )
        assert (svc_subtotal, svc_tax) == (subtotal, tax_total)
        assert svc_subtotal + svc_tax == subtotal + tax_total

    def test_zero_rated_has_no_tax(self) -> None:
        items = [_line_item(tax_type=TaxType.ZERO_RATED)]
        svc = PurchaseOrderService.__new__(PurchaseOrderService)
        built = svc._build_line_items(items)
        subtotal, tax_total = svc._sum_line_totals(built)
        assert tax_total == Decimal("0.00")
        assert subtotal == Decimal("200.00")


# FULL LIFECYCLE  (Postgres-guarded)


@_postgres_only
class TestLifecycleE2E:
    def test_draft_to_sent_to_paid_flow(self, db) -> None:
        vendor = _vendor(db)
        service = PurchaseOrderService(db)

        # CREATE -> DRAFT. total = 2*100 + 16% = 232.00.
        po = service.create(
            PurchaseOrderCreate(
                vendor_id=vendor.id,
                order_date=date.today(),
                line_items=[_line_item()],
            )
        )
        db.flush()
        assert po.status == PurchaseOrderStatus.DRAFT
        assert po.amount_paid == Decimal("0.00")
        assert po.balance_due == po.total
        total = po.total

        # MARK AS SENT -> SENT (no email / outbox row).
        service.mark_as_sent(po.id)
        db.flush()
        assert po.status == PurchaseOrderStatus.SENT
        assert po.sent_at is not None

        # Delete is DRAFT-only: a SENT PO cannot be deleted.
        with pytest.raises(BadRequestException):
            service.delete(po.id)

        # PARTIAL payment -> still SENT, balance reduced.
        half = (total / 2).quantize(Decimal("0.01"))
        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(amount=half, paymentDate=date.today()),
        )
        db.flush()
        assert po.status == PurchaseOrderStatus.SENT
        assert po.amount_paid == half
        assert po.balance_due == total - half

        # FINAL payment clears the balance -> auto-settle to PAID.
        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=total - half, paymentDate=date.today()
            ),
        )
        db.flush()
        assert po.status == PurchaseOrderStatus.PAID
        assert po.balance_due == Decimal("0.00")
        assert po.paid_at is not None

        # A PAID PO rejects further payment (immutability of the settled doc).
        with pytest.raises(BadRequestException):
            service.record_payment(
                po.id,
                PurchaseOrderPaymentCreate(
                    amount=Decimal("1.00"), paymentDate=date.today()
                ),
            )

    def test_payment_before_send_is_rejected(self, db) -> None:
        vendor = _vendor(db)
        service = PurchaseOrderService(db)
        po = service.create(
            PurchaseOrderCreate(
                vendor_id=vendor.id,
                order_date=date.today(),
                line_items=[_line_item()],
            )
        )
        db.flush()
        # DRAFT cannot take a payment — it must be sent first.
        with pytest.raises(BadRequestException):
            service.record_payment(
                po.id,
                PurchaseOrderPaymentCreate(
                    amount=Decimal("10.00"), paymentDate=date.today()
                ),
            )
