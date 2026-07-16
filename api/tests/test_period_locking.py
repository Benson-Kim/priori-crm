"""Period locking (issue #8): closed periods block financial writes.

Contract pinned here:
- the guard is a no-op with no periods, an open period, or a None date;
- a closed period covering the date raises BadRequestException with the
  offending field name;
- periods cannot overlap; close/reopen are audited; reopening requires a
  justification and only applies to closed periods;
- document create/update paths across invoices, quotes, expenses and
  purchase orders reject dates inside a closed period, while open-period
  writes are unaffected.

Create paths use advisory-lock reference generation, so the DB-backed
document cases are PostgreSQL-only; guard/service unit cases run
everywhere.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.common.audit import AuditEvent
from app.common.exceptions import BadRequestException
from app.common.period_guard import assert_period_open
from app.constants.enums import (
    Currency,
    CustomerStatus,
    CustomerType,
    TaxType,
    VendorStatus,
)
from app.modules.customers.models import Customer
from app.modules.invoices.schemas import InvoiceCreate, InvoiceUpdate
from app.modules.invoices.service import InvoiceService
from app.modules.periods.schemas import PeriodCreate, PeriodReopenRequest
from app.modules.periods.service import PeriodsService
from app.modules.quotes.schemas import QuoteCreate
from app.modules.quotes.service import QuoteService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

pg_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Create uses advisory-lock reference generation (PostgreSQL only)",
)


# fixtures / builders


def _customer(db) -> Customer:
    customer = Customer(
        customer_type=CustomerType.INDIVIDUAL,
        status=CustomerStatus.ACTIVE,
        first_name="Test",
        last_name="Customer",
        email=f"c-{uuid.uuid4().hex[:8]}@example.com",
        phone="+254700000000",
        currency=Currency.KES,
        balance=Decimal("0.00"),
    )
    db.add(customer)
    db.flush()
    return customer


def _vendor(db) -> Vendor:
    vendor = Vendor(
        vendor_name="Acme Supplies",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line() -> dict:
    return {
        "item_name": "Item",
        "description": "desc",
        "quantity": 2,
        "unit_price": "100.00",
        "tax_type": TaxType.NO_TAX.value,
    }


def _invoice_payload(customer_id, on_date: date) -> InvoiceCreate:
    return InvoiceCreate.model_validate(
        {
            "customer_id": str(customer_id),
            "transaction_date": on_date.isoformat(),
            "due_date": (on_date + timedelta(days=30)).isoformat(),
            "line_items": [_line()],
        }
    )


def _quote_payload(customer_id, on_date: date) -> QuoteCreate:
    return QuoteCreate.model_validate(
        {
            "customer_id": str(customer_id),
            "transaction_date": on_date.isoformat(),
            "due_date": (on_date + timedelta(days=30)).isoformat(),
            "line_items": [_line()],
        }
    )


def _period(start: date, end: date) -> PeriodCreate:
    return PeriodCreate(start_date=start, end_date=end)


def _closed_window(db, center: date, days: int = 5):
    """Create and immediately close a period around ``center``."""
    svc = PeriodsService(db)
    period = svc.create(
        _period(center - timedelta(days=days), center + timedelta(days=days))
    )
    svc.close(period.id)
    return period


# guard unit tests


class TestPeriodGuard:
    def test_noop_without_periods(self, db):
        assert_period_open(db, date.today())  # must not raise

    def test_none_date_is_ignored(self, db):
        assert_period_open(db, None)  # must not raise

    def test_open_period_allows(self, db):
        d = date.today() - timedelta(days=40)
        PeriodsService(db).create(_period(d - timedelta(days=5), d + timedelta(days=5)))
        assert_period_open(db, d)  # open period: must not raise

    def test_closed_period_blocks_with_field_name(self, db):
        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        with pytest.raises(BadRequestException):
            assert_period_open(db, d, field="payment_date")

    def test_date_outside_closed_period_allows(self, db):
        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        assert_period_open(db, date.today())  # outside the window


# periods service


class TestPeriodsService:
    def test_overlapping_period_rejected(self, db):
        svc = PeriodsService(db)
        d = date.today() - timedelta(days=40)
        svc.create(_period(d - timedelta(days=5), d + timedelta(days=5)))
        with pytest.raises(BadRequestException):
            svc.create(_period(d, d + timedelta(days=10)))

    def test_close_writes_audit_event(self, db):
        d = date.today() - timedelta(days=40)
        period = _closed_window(db, d)
        rows = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "accounting_period",
                AuditEvent.entity_id == period.id,
                AuditEvent.action == "period_closed",
            )
            .all()
        )
        assert len(rows) == 1

    def test_close_already_closed_rejected(self, db):
        d = date.today() - timedelta(days=40)
        period = _closed_window(db, d)
        with pytest.raises(BadRequestException):
            PeriodsService(db).close(period.id)

    def test_reopen_requires_closed_period(self, db):
        svc = PeriodsService(db)
        d = date.today() - timedelta(days=40)
        period = svc.create(_period(d - timedelta(days=5), d + timedelta(days=5)))
        with pytest.raises(BadRequestException):
            svc.reopen(period.id, "typo in window")

    def test_reopen_writes_audit_and_stores_reason(self, db):
        d = date.today() - timedelta(days=40)
        period = _closed_window(db, d)
        reopened = PeriodsService(db).reopen(period.id, "missed vendor bill")
        assert reopened.status == "open"
        assert reopened.reopen_reason == "missed vendor bill"
        rows = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "accounting_period",
                AuditEvent.entity_id == period.id,
                AuditEvent.action == "period_reopened",
            )
            .all()
        )
        assert len(rows) == 1

    def test_reopen_reason_is_mandatory_at_schema_level(self):
        with pytest.raises(ValidationError):
            PeriodReopenRequest(reason="")


# document write paths (PostgreSQL-backed)


@pg_only
class TestDocumentWritesBlocked:
    def test_invoice_create_into_closed_period_rejected(self, db):
        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        customer = _customer(db)
        with pytest.raises(BadRequestException):
            InvoiceService(db).create(_invoice_payload(customer.id, d))

    def test_invoice_create_today_still_allowed(self, db):
        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        customer = _customer(db)
        invoice = InvoiceService(db).create(_invoice_payload(customer.id, date.today()))
        assert invoice.id is not None

    def test_quote_create_into_closed_period_rejected(self, db):
        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        customer = _customer(db)
        with pytest.raises(BadRequestException):
            QuoteService(db).create(_quote_payload(customer.id, d))

    def test_expense_create_into_closed_period_rejected(self, db):
        from app.modules.expenses.schemas import ExpenseCreate
        from app.modules.expenses.service import ExpenseService

        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        vendor = _vendor(db)
        payload = ExpenseCreate.model_validate(
            {
                "vendor_id": str(vendor.id),
                "expense_date": d.isoformat(),
                "due_date": (d + timedelta(days=30)).isoformat(),
                "line_items": [_line()],
            }
        )
        with pytest.raises(BadRequestException):
            ExpenseService(db).create(payload)

    def test_po_create_into_closed_period_rejected(self, db):
        from app.modules.purchase_orders.schemas import (
            PurchaseOrderCreate,
            PurchaseOrderLineItemCreate,
        )
        from app.modules.purchase_orders.service import PurchaseOrderService

        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        vendor = _vendor(db)
        payload = PurchaseOrderCreate(
            vendor_id=vendor.id,
            order_date=d,
            line_items=[
                PurchaseOrderLineItemCreate(
                    item_name="Item",
                    description="desc",
                    quantity=Decimal("2"),
                    unit_price=Decimal("100.00"),
                    tax_type=TaxType.NO_TAX,
                )
            ],
        )
        with pytest.raises(BadRequestException):
            PurchaseOrderService(db).create(payload)

    def test_invoice_update_blocked_when_persisted_date_now_closed(self, db):
        d = date.today() - timedelta(days=40)
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(_invoice_payload(customer.id, d))
        _closed_window(db, d)
        with pytest.raises(BadRequestException):
            service.update(
                invoice.id,
                InvoiceUpdate.model_validate({"notes": "late edit"}),
            )

    def test_invoice_update_cannot_move_date_into_closed_period(self, db):
        d = date.today() - timedelta(days=40)
        _closed_window(db, d)
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(_invoice_payload(customer.id, date.today()))
        with pytest.raises(BadRequestException):
            service.update(
                invoice.id,
                InvoiceUpdate.model_validate({"transaction_date": d.isoformat()}),
            )
