"""Tests for DB-enforced optimistic locking (W2.5, P-9).

The version check is now atomic: assert_version re-selects the row WITH FOR
UPDATE before comparing, so a stale writer is rejected rather than silently
overwriting. The stale-version logic test runs everywhere; the true
two-session concurrency test is Postgres-only (FOR UPDATE row locks).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import ConflictException
from app.constants.enums import Currency, CustomerStatus, InvoiceStatus, VendorStatus
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice
from app.modules.invoices.schemas import InvoiceUpdate
from app.modules.invoices.service import InvoiceService
from app.modules.vendors.models import Vendor
from app.modules.vendors.schemas import VendorUpdate
from app.modules.vendors.service import VendorService

from tests.conftest import USING_POSTGRES, TestingSessionLocal


def _customer(db, email="lock@acme.test") -> Customer:
    c = Customer(
        customer_type="business",
        company_name="Acme",
        first_name="Ada",
        last_name="L",
        email=email,
        phone="+254700000000",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(c)
    db.flush()
    return c


def _invoice(db, customer, number="INV-LOCK-1") -> Invoice:
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        invoice_reference=number.replace("INV", "IN"),
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
        version=1,
    )
    db.add(inv)
    db.flush()
    return inv


def _vendor(db, name="Acme Supplies") -> Vendor:
    v = Vendor(
        vendor_name=name,
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(v)
    db.flush()
    return v


class TestStaleVersionRejected:
    def test_invoice_stale_version_conflicts(self, db):
        customer = _customer(db, email="inv-lock@acme.test")
        invoice = _invoice(db, customer)
        service = InvoiceService(db)

        # First edit at the current version succeeds and bumps to v2.
        service.update(invoice.id, InvoiceUpdate(notes="first"), expected_version=1)
        assert invoice.version == 2

        # A second writer still holding v1 must be rejected, not silently win.
        with pytest.raises(ConflictException):
            service.update(invoice.id, InvoiceUpdate(notes="stale"), expected_version=1)

    def test_vendor_stale_version_conflicts(self, db):
        vendor = _vendor(db)
        service = VendorService(db)

        service.update(vendor.id, VendorUpdate(notes="first"), expected_version=1)
        assert vendor.version == 2

        with pytest.raises(ConflictException):
            service.update(vendor.id, VendorUpdate(notes="stale"), expected_version=1)

    def test_matching_version_succeeds(self, db):
        customer = _customer(db, email="ok-lock@acme.test")
        invoice = _invoice(db, customer, number="INV-LOCK-OK")
        service = InvoiceService(db)

        service.update(invoice.id, InvoiceUpdate(notes="v1"), expected_version=1)
        # Re-read current version and edit again — should pass.
        service.update(invoice.id, InvoiceUpdate(notes="v2"), expected_version=invoice.version)
        assert invoice.version == 3


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="True concurrency needs FOR UPDATE row locks (PostgreSQL).",
)
class TestTrueConcurrency:
    def test_two_sessions_second_writer_conflicts(self, db):
        """Two independent sessions both read v1; only the first wins."""
        customer = _customer(db, email="concurrent@acme.test")
        invoice = _invoice(db, customer, number="INV-CONC-1")
        db.commit()  # make the row visible to other sessions

        invoice_id = invoice.id
        session_a = TestingSessionLocal()
        session_b = TestingSessionLocal()
        try:
            svc_a = InvoiceService(session_a)
            svc_b = InvoiceService(session_b)

            # Writer A updates at v1 and commits -> v2.
            svc_a.update(invoice_id, InvoiceUpdate(notes="A"), expected_version=1)
            session_a.commit()

            # Writer B still holds v1; its atomic guard must reject.
            with pytest.raises(ConflictException):
                svc_b.update(invoice_id, InvoiceUpdate(notes="B"), expected_version=1)
                session_b.commit()
            session_b.rollback()
        finally:
            session_a.close()
            session_b.close()
            # Clean up the committed row so the autouse drop_all teardown is clean.
            db.query(Invoice).filter(Invoice.id == invoice_id).delete()
            db.query(Customer).filter(Customer.id == customer.id).delete()
            db.commit()
