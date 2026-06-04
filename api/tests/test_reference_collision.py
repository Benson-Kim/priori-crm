"""Tests for collision-safe reference generation (W2.2, P-4).

The COUNT(*) strategy reuses a number after the latest record is hard-deleted
(count drops by one), colliding on the next insert. The MAX(numeric suffix)+1
strategy never reuses a deleted number. These tests reproduce the collision and
prove the new MAX default avoids it.

substring()/cast() and pg_advisory_xact_lock are PostgreSQL constructs, so the
tests run only against the CI Postgres service (W1.2); on the SQLite fallback
the advisory lock and substring semantics differ and the collision scenario is
not representative.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.reference import ReferenceGenerator
from app.constants.enums import Currency, CustomerStatus, InvoiceStatus
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice

from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="MAX strategy relies on Postgres substring/cast + advisory lock.",
)


def _customer(db) -> Customer:
    c = Customer(
        customer_type="business",
        company_name="Acme",
        first_name="Ada",
        last_name="L",
        email="ada.ref@acme.test",
        phone="+254700000000",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(c)
    db.flush()
    return c


def _invoice(db, customer, *, number: str, reference: str) -> Invoice:
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        invoice_reference=reference,
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=InvoiceStatus.DRAFT,
        subtotal=Decimal("10.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("10.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("10.00"),
    )
    db.add(inv)
    db.flush()
    return inv


def _day_prefix() -> str:
    return f"INV-{date.today().strftime('%Y%m%d')}"


def test_date_scoped_max_avoids_collision_after_delete(db):
    gen = ReferenceGenerator(db)
    customer = _customer(db)
    prefix = _day_prefix()

    _invoice(db, customer, number=f"{prefix}-001", reference="IN-0001")
    latest = _invoice(db, customer, number=f"{prefix}-002", reference="IN-0002")

    # Hard-delete the latest record.
    db.delete(latest)
    db.flush()

    # Default (MAX) must skip the deleted suffix.
    next_max = gen.generate(
        model=Invoice,
        column=Invoice.invoice_number,
        prefix="INV",
        lock_key="invoice_number",
        width=3,
        use_date_scope=True,
    )
    assert next_max == f"{prefix}-003"

    # Legacy COUNT reuses the freed suffix -> collision with the surviving 001? No:
    # one row remains, so COUNT+1 = 002, which collides with the just-deleted
    # number's neighbour space and would clash once 002 is recreated.
    next_count = gen.generate(
        model=Invoice,
        column=Invoice.invoice_number,
        prefix="INV",
        lock_key="invoice_number",
        width=3,
        use_date_scope=True,
        use_max_strategy=False,
    )
    assert next_count == f"{prefix}-002"
    assert next_count != next_max


def test_global_reference_max_avoids_collision_after_delete(db):
    gen = ReferenceGenerator(db)
    customer = _customer(db)
    prefix = _day_prefix()

    _invoice(db, customer, number=f"{prefix}-001", reference="IN-0001")
    latest = _invoice(db, customer, number=f"{prefix}-002", reference="IN-0002")

    db.delete(latest)
    db.flush()

    next_ref = gen.generate(
        model=Invoice,
        column=Invoice.invoice_reference,
        prefix="IN",
        lock_key="invoice_reference_gen",
        width=4,
        use_date_scope=False,
    )
    assert next_ref == "IN-0003"
