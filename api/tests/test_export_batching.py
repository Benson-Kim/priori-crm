"""Tests for batched export loaders (W3.2, P-5).

The export endpoints previously did ``[service.get_by_id(item.id) for item in
result.items]`` - one fully-joined fetch per row (N+1). ``list_for_export``
replaces that with a single query (customer/vendor eager-joined; line items via
selectinload). These tests verify it returns full ORM rows with line items
loaded, applies filters, and honours the limit.

The loaders use a SQL join + selectinload; run them against Postgres to match
production semantics (gen_random_uuid(), UUID columns).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.constants.enums import Currency, CustomerStatus, InvoiceStatus
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.invoices.schemas import InvoiceFilterParams
from app.modules.invoices.service import InvoiceService
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Export loaders use Postgres UUID columns + server defaults.",
)


def _customer(db) -> Customer:
    c = Customer(
        customer_type="business",
        company_name="Acme Export",
        first_name="Ada",
        last_name="L",
        email="ada.export@acme.test",
        phone="+254700000000",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(c)
    db.flush()
    return c


def _invoice(
    db, customer, *, number: str, reference: str, with_line: bool = True
) -> Invoice:
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        invoice_reference=reference,
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=InvoiceStatus.DRAFT,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("100.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("100.00"),
    )
    db.add(inv)
    db.flush()
    if with_line:
        db.add(
            InvoiceLineItem(
                invoice_id=inv.id,
                line_number=1,
                item_name="Widget",
                description="A widget",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                tax_type="no_tax",
                tax_amount=Decimal("0.00"),
                line_total=Decimal("100.00"),
            )
        )
        db.flush()
    return inv


def test_list_for_export_loads_line_items(db):
    service = InvoiceService(db)
    customer = _customer(db)
    today = date.today().strftime("%Y%m%d")
    _invoice(db, customer, number=f"INV-{today}-001", reference="IN-0001")
    _invoice(db, customer, number=f"INV-{today}-002", reference="IN-0002")

    rows = service.list_for_export(InvoiceFilterParams(), include_line_items=True)

    assert len(rows) == 2
    # Full ORM objects with line items + customer available (no extra fetch).
    for row in rows:
        assert isinstance(row, Invoice)
        assert len(row.line_items) == 1
        assert row.customer is not None


def test_list_for_export_respects_status_filter(db):
    service = InvoiceService(db)
    customer = _customer(db)
    today = date.today().strftime("%Y%m%d")
    draft = _invoice(db, customer, number=f"INV-{today}-001", reference="IN-0001")
    sent = _invoice(db, customer, number=f"INV-{today}-002", reference="IN-0002")
    sent.status = InvoiceStatus.SENT
    db.flush()

    rows = service.list_for_export(InvoiceFilterParams(status="sent"))
    ids = {r.id for r in rows}
    assert sent.id in ids
    assert draft.id not in ids


def test_list_for_export_honours_limit(db):
    service = InvoiceService(db)
    customer = _customer(db)
    today = date.today().strftime("%Y%m%d")
    for i in range(1, 4):
        _invoice(
            db,
            customer,
            number=f"INV-{today}-00{i}",
            reference=f"IN-000{i}",
            with_line=False,
        )

    rows = service.list_for_export(InvoiceFilterParams(), limit=2)
    assert len(rows) == 2
