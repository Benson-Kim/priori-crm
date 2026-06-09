"""Tests for the W2.6 data-integrity edge cases (P-... / SH-BE-2/3, W-6,
VER-NEW-1/2, CUST-DBA-2).
"""

from decimal import Decimal

import pytest

from app.common.exceptions import ConflictException
from app.common.financial import build_line_items, get_tax_rate
from app.common.validators import normalize_phone
from app.constants.enums import Currency, CustomerType, InvoiceStatus, TaxType


class TestTaxRate:
    def test_valid_tax_type_resolves(self):
        assert get_tax_rate(TaxType.VAT_16) == Decimal("0.16")
        assert get_tax_rate(TaxType.NO_TAX) == Decimal("0.00")

    def test_unknown_tax_type_raises(self):
        with pytest.raises(ValueError):
            get_tax_rate("vat_99")  # not a TaxType member


class TestBuildLineItems:
    def test_missing_required_field_raises(self):
        with pytest.raises(ValueError):
            build_line_items(
                [
                    {
                        "item_name": "Widget",
                        "description": "x",
                        # quantity missing
                        "unit_price": Decimal("10.00"),
                        "tax_type": TaxType.NO_TAX,
                    }
                ]
            )

    def test_complete_item_builds(self):
        built = build_line_items(
            [
                {
                    "item_name": "Widget",
                    "description": "x",
                    "quantity": Decimal("2"),
                    "unit_price": Decimal("10.00"),
                    "tax_type": TaxType.NO_TAX,
                }
            ]
        )
        assert built[0]["line_total"] == Decimal("20.00")


class TestNormalizePhone:
    def test_valid_kenyan_local_normalizes(self):
        assert normalize_phone("0712345678", "KE") == "+254712345678"

    def test_valid_international_normalizes(self):
        assert normalize_phone("+254712345678", "KE") == "+254712345678"

    def test_national_number_normalizes(self):
        assert normalize_phone("712345678", "KE") == "+254712345678"

    @pytest.mark.parametrize("bad", ["abc", "123", "", "   ", "55"])
    def test_malformed_rejected_not_coerced(self, bad):
        with pytest.raises(ValueError):
            normalize_phone(bad, "KE")


class TestDeleteEligibilityPartial:
    def test_partial_invoice_customer_not_hard_deletable(self, db):
        from datetime import date, timedelta

        from app.modules.customers.models import Customer
        from app.modules.customers.service import CustomerService
        from app.modules.invoices.models import Invoice

        customer = Customer(
            customer_type=CustomerType.BUSINESS,
            company_name="Acme",
            first_name="Ada",
            last_name="L",
            email="partial@acme.test",
            phone="+254712345678",
            currency=Currency.KES,
        )
        db.add(customer)
        db.flush()

        today = date.today()
        db.add(
            Invoice(
                invoice_number="INV-PART-1",
                invoice_reference="IN-PART1",
                customer_id=customer.id,
                transaction_date=today,
                due_date=today + timedelta(days=30),
                currency=Currency.KES,
                status=InvoiceStatus.PARTIAL,
                subtotal=Decimal("100.00"),
                tax_total=Decimal("0.00"),
                total_due=Decimal("100.00"),
                amount_paid=Decimal("40.00"),
                balance_due=Decimal("60.00"),
            )
        )
        db.flush()

        result = CustomerService(db).check_delete_eligibility(customer.id)
        assert result["can_hard_delete"] is False


class TestCustomerEmailCaseInsensitive:
    def test_email_lowercased_on_create_and_duplicate_conflicts(self, db):
        from app.modules.customers.schemas import CustomerCreate
        from app.modules.customers.service import CustomerService

        service = CustomerService(db)

        def _payload(email):
            return CustomerCreate(
                customerType=CustomerType.BUSINESS,
                companyName="Acme",
                firstName="Ada",
                lastName="Lovelace",
                email=email,
                phone="+254712345678",
                address="123 Main Street",
                country="KE",
                province="Nairobi",
                city="Nairobi",
                postalCode="00100",
            )

        created = service.create(_payload("Alpha@x.com"))
        assert created.email == "alpha@x.com"

        # A mixed-case duplicate must be rejected.
        with pytest.raises(ConflictException):
            service.create(_payload("ALPHA@x.com"))
