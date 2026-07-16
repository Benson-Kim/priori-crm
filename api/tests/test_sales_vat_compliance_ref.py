"""Sales-document VAT compliance reference (invoices + quotes).

The sales editor sends ``vatComplianceRef`` but until now the server
ignored it (PO-only column). These tests pin the persistence contract,
mirroring the PO behaviour (PO-27 Option A):

- an explicitly supplied reference is persisted verbatim;
- when omitted and VAT is enabled, it defaults from the owner profile's
  tax PIN so issued documents carry our own registration number;
- when VAT is disabled and nothing is sent, it stays NULL;
- a blank explicit value normalises to NULL;
- the reference is editable while the document is editable.

Also pins the owner ``vat_rate`` precision widening (Numeric(5,4)) so a
default like 16.5% round-trips without loss.

Create paths use advisory-lock reference generation, so the DB-backed
cases are PostgreSQL-only; the owner-precision case runs everywhere.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.constants.enums import Currency, CustomerStatus, CustomerType, TaxType
from app.modules.customers.models import Customer
from app.modules.invoices.schemas import InvoiceCreate, InvoiceUpdate
from app.modules.invoices.service import InvoiceService
from app.modules.owner.schemas import OwnerProfileUpdate
from app.modules.owner.service import OwnerService
from app.modules.quotes.schemas import QuoteCreate
from app.modules.quotes.service import QuoteService
from tests.conftest import USING_POSTGRES

VAT_16 = Decimal("0.16")

pg_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Create uses advisory-lock reference generation (PostgreSQL only)",
)


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


def _seed_owner_pin(db, pin: str = "OWNER-PIN-999") -> None:
    owner = OwnerService(db)
    owner.get_or_create()
    owner.update(OwnerProfileUpdate(fullName="Acme Ltd", taxPin=pin))
    db.flush()


def _line() -> dict:
    return {
        "itemName": "Item",
        "description": "desc",
        "quantity": 2,
        "unitPrice": "100.00",
        "taxType": TaxType.NO_TAX.value,
    }


def _invoice_payload(customer_id, **kw) -> InvoiceCreate:
    base = {
        "customerId": str(customer_id),
        "dueDate": (date.today() + timedelta(days=30)).isoformat(),
        "lineItems": [_line()],
    }
    base.update(kw)
    return InvoiceCreate.model_validate(base)


def _quote_payload(customer_id, **kw) -> QuoteCreate:
    base = {
        "customerId": str(customer_id),
        "dueDate": (date.today() + timedelta(days=30)).isoformat(),
        "lineItems": [_line()],
    }
    base.update(kw)
    return QuoteCreate.model_validate(base)


@pg_only
class TestInvoiceVatComplianceRef:
    def test_explicit_ref_is_persisted(self, db):
        _seed_owner_pin(db)
        customer = _customer(db)
        invoice = InvoiceService(db).create(
            _invoice_payload(
                customer.id,
                vatEnabled=True,
                vatRate="0.16",
                vatComplianceRef="CUSTOM-REF-42",
            )
        )
        assert invoice.vat_compliance_ref == "CUSTOM-REF-42"

    def test_defaults_from_owner_tax_pin_when_vat_enabled(self, db):
        _seed_owner_pin(db, pin="OWNER-PIN-999")
        customer = _customer(db)
        invoice = InvoiceService(db).create(
            _invoice_payload(customer.id, vatEnabled=True, vatRate="0.16")
        )
        assert invoice.vat_compliance_ref == "OWNER-PIN-999"

    def test_stays_null_when_vat_disabled_and_omitted(self, db):
        _seed_owner_pin(db)
        customer = _customer(db)
        invoice = InvoiceService(db).create(
            _invoice_payload(customer.id, vatEnabled=False)
        )
        assert invoice.vat_compliance_ref is None

    def test_blank_explicit_ref_normalises_to_null(self, db):
        _seed_owner_pin(db)
        customer = _customer(db)
        invoice = InvoiceService(db).create(
            _invoice_payload(
                customer.id,
                vatEnabled=True,
                vatRate="0.16",
                vatComplianceRef="   ",
            )
        )
        assert invoice.vat_compliance_ref is None

    def test_update_changes_ref_while_editable(self, db):
        _seed_owner_pin(db)
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(
            _invoice_payload(customer.id, vatEnabled=True, vatRate="0.16")
        )
        updated = service.update(
            invoice.id,
            InvoiceUpdate.model_validate({"vatComplianceRef": "NEW-REF-1"}),
        )
        assert updated.vat_compliance_ref == "NEW-REF-1"


@pg_only
class TestQuoteVatComplianceRef:
    def test_explicit_ref_is_persisted(self, db):
        _seed_owner_pin(db)
        customer = _customer(db)
        quote = QuoteService(db).create(
            _quote_payload(
                customer.id,
                vatEnabled=True,
                vatRate="0.16",
                vatComplianceRef="CUSTOM-REF-42",
            )
        )
        assert quote.vat_compliance_ref == "CUSTOM-REF-42"

    def test_defaults_from_owner_tax_pin_when_vat_enabled(self, db):
        _seed_owner_pin(db, pin="OWNER-PIN-999")
        customer = _customer(db)
        quote = QuoteService(db).create(
            _quote_payload(customer.id, vatEnabled=True, vatRate="0.16")
        )
        assert quote.vat_compliance_ref == "OWNER-PIN-999"


class TestOwnerVatRatePrecision:
    def test_fractional_rate_with_four_decimals_round_trips(self, db):
        owner = OwnerService(db)
        owner.get_or_create()
        owner.update(
            OwnerProfileUpdate(fullName="Acme Ltd", vatEnabled=True, vatRate=0.165)
        )
        db.flush()
        profile = owner.get_or_create()
        assert float(profile.vat_rate) == pytest.approx(0.165)
