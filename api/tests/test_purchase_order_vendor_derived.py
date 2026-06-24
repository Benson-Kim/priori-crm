"""Tests for the tightened Purchase Order create/update contract.

The PO flow no longer accepts currency, isRecurring or complianceRef from the
client: currency and the compliance / eTIMS reference are derived from the
selected vendor server-side, and the recurring flag was removed. These tests
prove the backend enforces that so it can never drift looser than the UI.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.constants.enums import Currency, PurchaseOrderStatus, TaxType, VendorStatus
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderUpdate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor


def _vendor(
    db,
    *,
    currency=Currency.KES,
    tax_id_pin="P051234567X",
    email="vendor@acme.test",
    name="Acme Supplies",
) -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        email=email,
        currency=currency,
        tax_id_pin=tax_id_pin,
        status=VendorStatus.ACTIVE,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line_items():
    return [
        PurchaseOrderLineItemCreate(
            itemName="Steel bolts M8",
            description="Box of 500 grade-8.8 bolts",
            quantity=Decimal("10"),
            unitPrice=Decimal("1200.00"),
            taxType=TaxType.NO_TAX,
        )
    ]


def _create_payload(vendor_id) -> PurchaseOrderCreate:
    return PurchaseOrderCreate(
        vendorId=vendor_id,
        orderDate=date.today(),
        lineItems=_line_items(),
    )


class TestCreateDerivesFromVendor:
    def test_currency_pinned_to_vendor(self, db):
        vendor = _vendor(db, currency=Currency.USD, email="usd@acme.test")
        po = PurchaseOrderService(db).create(_create_payload(vendor.id))
        assert po.currency == Currency.USD

    def test_compliance_ref_taken_from_vendor_tax_id_pin(self, db):
        vendor = _vendor(db, tax_id_pin="P051234567X", email="pin@acme.test")
        po = PurchaseOrderService(db).create(_create_payload(vendor.id))
        assert po.compliance_ref == "P051234567X"

    def test_compliance_ref_none_when_vendor_has_no_pin(self, db):
        vendor = _vendor(db, tax_id_pin=None, email="nopin@acme.test")
        po = PurchaseOrderService(db).create(_create_payload(vendor.id))
        assert po.compliance_ref is None

    def test_is_recurring_always_false(self, db):
        vendor = _vendor(db, email="rec@acme.test")
        po = PurchaseOrderService(db).create(_create_payload(vendor.id))
        assert po.is_recurring is False


class TestWritableSurfaceRejectsRemovedFields:
    """currency / isRecurring / complianceRef are not part of the schema, so
    Pydantic rejects them as unknown fields (forbidden by the generated
    contract) rather than silently accepting them."""

    def test_create_rejects_currency(self):
        with pytest.raises(ValidationError):
            PurchaseOrderCreate(
                vendorId="123e4567-e89b-12d3-a456-426614174000",
                orderDate=date.today(),
                lineItems=_line_items(),
                currency=Currency.KES,
            )

    def test_create_rejects_is_recurring(self):
        with pytest.raises(ValidationError):
            PurchaseOrderCreate(
                vendorId="123e4567-e89b-12d3-a456-426614174000",
                orderDate=date.today(),
                lineItems=_line_items(),
                isRecurring=True,
            )

    def test_create_rejects_compliance_ref(self):
        with pytest.raises(ValidationError):
            PurchaseOrderCreate(
                vendorId="123e4567-e89b-12d3-a456-426614174000",
                orderDate=date.today(),
                lineItems=_line_items(),
                complianceRef="LPO-2026-0042",
            )

    def test_update_rejects_compliance_ref(self):
        with pytest.raises(ValidationError):
            PurchaseOrderUpdate(complianceRef="LPO-2026-0042")

    def test_update_rejects_is_recurring(self):
        with pytest.raises(ValidationError):
            PurchaseOrderUpdate(isRecurring=True)


class TestUpdateReDerivesComplianceOnVendorChange:
    def test_vendor_change_redrives_compliance_ref(self, db):
        original_vendor = _vendor(
            db,
            tax_id_pin="P051111111A",
            email="orig@acme.test",
            name="Original Vendor",
        )
        new_vendor = _vendor(
            db,
            tax_id_pin="P052222222B",
            email="new@acme.test",
            name="New Vendor",
        )
        service = PurchaseOrderService(db)
        po = service.create(_create_payload(original_vendor.id))
        assert po.compliance_ref == "P051111111A"

        updated = service.update(
            po.id, PurchaseOrderUpdate(vendorId=new_vendor.id)
        )
        assert updated.vendor_id == new_vendor.id
        assert updated.compliance_ref == "P052222222B"
        # The PO stays DRAFT and editable throughout.
        assert updated.status == PurchaseOrderStatus.DRAFT
