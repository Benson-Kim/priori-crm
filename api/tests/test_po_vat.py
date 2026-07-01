"""
PO-level VAT (PO-27).

VAT for a purchase order is a PO-level toggle computed on the subtotal, not a
per-line tax. This module pins the acceptance criteria:

- VAT on @ rate: tax_total = round(subtotal * rate); total = subtotal + tax.
- VAT off: tax_total = 0; total = subtotal; lines persist no_tax/0.
- vat_rate is required when vat_enabled (schema + service guard).
- vat_compliance_ref defaults from the owner profile's tax_pin (Option A) and
  is editable per PO; the vendor-derived compliance_ref stays separate.
- update() recomputes on toggling VAT / changing the rate, keeps the
  amount-paid floor guard, and preserves the single version bump.
- Overpayment still drives balance_due negative with VAT applied.
- /calculate preview matches what create() persists for identical input.

The create/update paths use advisory-lock reference generation, so the
DB-backed cases are PostgreSQL-only; the pure schema/preview cases run
everywhere.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.common.exceptions import BadRequestException
from app.constants.enums import Currency, PurchaseOrderStatus, TaxType, VendorStatus
from app.modules.owner.schemas import OwnerProfileUpdate
from app.modules.owner.service import OwnerService
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderPaymentCreate,
    PurchaseOrderUpdate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

VAT_16 = Decimal("0.16")

pg_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="PO create uses advisory-lock reference generation (PostgreSQL only)",
)


def _vendor(db, *, currency=Currency.KES, tax_id_pin="VENDOR-PIN-001") -> Vendor:
    vendor = Vendor(
        vendor_name="Acme Supplies",
        currency=currency,
        status=VendorStatus.ACTIVE,
        tax_id_pin=tax_id_pin,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line(**kw) -> PurchaseOrderLineItemCreate:
    base = {
        "item_name": "Item",
        "description": "desc",
        "quantity": Decimal("2"),
        "unit_price": Decimal("100.00"),
        "tax_type": TaxType.NO_TAX,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


def _create_payload(**kw) -> PurchaseOrderCreate:
    # vendor_id is required by the schema. DB-backed tests pass a real vendor's
    # id (overriding this default); the pure-schema tests only need any UUID.
    base = {
        "vendor_id": uuid.uuid4(),
        "order_date": date.today(),
        "line_items": [_line()],
    }
    base.update(kw)
    return PurchaseOrderCreate(**base)


# SCHEMA GUARD


class TestVatSchemaValidation:
    def test_rate_required_when_enabled(self):
        # A VAT rate is mandatory when the toggle is on.
        with pytest.raises(ValidationError):
            _create_payload(vat_enabled=True)

    def test_disabled_without_rate_is_valid(self):
        payload = _create_payload(vat_enabled=False)
        assert payload.vat_enabled is False
        assert payload.vat_rate is None

    def test_rate_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            _create_payload(vat_enabled=True, vat_rate=Decimal("1.5"))


# CREATE-TIME VAT COMPUTATION


@pg_only
class TestVatTotalsOnCreate:
    def test_vat_on_computes_tax_on_subtotal(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, vat_enabled=True, vat_rate=VAT_16)
        )
        assert po.subtotal == Decimal("200.00")
        assert po.tax_total == Decimal("32.00")
        assert po.total == Decimal("232.00")
        # Lines never carry per-line tax under PO-level VAT.
        assert all(li.tax_type == TaxType.NO_TAX.value for li in po.line_items)
        assert all(li.tax_amount == Decimal("0.00") for li in po.line_items)

    def test_vat_off_has_no_tax(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id, vat_enabled=False))
        assert po.tax_total == Decimal("0.00")
        assert po.total == po.subtotal == Decimal("200.00")
        assert po.vat_rate is None


# vat_compliance_ref DEFAULTING (Option A)


@pg_only
class TestVatComplianceRefDefault:
    def test_defaults_from_owner_tax_pin(self, db):
        # Seed an owner profile with a tax PIN; the PO's vat_compliance_ref
        # must default from it when the client omits it.
        owner = OwnerService(db)
        owner.get_or_create()
        owner.update(OwnerProfileUpdate(fullName="Acme Ltd", taxPin="OWNER-PIN-999"))
        db.flush()

        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, vat_enabled=True, vat_rate=VAT_16)
        )
        assert po.vat_compliance_ref == "OWNER-PIN-999"

    def test_explicit_ref_overrides_owner_default(self, db):
        owner = OwnerService(db)
        owner.get_or_create()
        owner.update(OwnerProfileUpdate(fullName="Acme Ltd", taxPin="OWNER-PIN-999"))
        db.flush()

        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(
                vendor_id=vendor.id,
                vat_enabled=True,
                vat_rate=VAT_16,
                vat_compliance_ref="CUSTOM-REF-42",
            )
        )
        assert po.vat_compliance_ref == "CUSTOM-REF-42"

    def test_vendor_compliance_ref_stays_separate(self, db):
        # The vendor-derived compliance_ref (PO metadata) is unchanged by the
        # new owner-derived vat_compliance_ref.
        owner = OwnerService(db)
        owner.get_or_create()
        owner.update(OwnerProfileUpdate(fullName="Acme Ltd", taxPin="OWNER-PIN-999"))
        db.flush()

        vendor = _vendor(db, tax_id_pin="VENDOR-PIN-777")
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, vat_enabled=True, vat_rate=VAT_16)
        )
        assert po.compliance_ref == "VENDOR-PIN-777"
        assert po.vat_compliance_ref == "OWNER-PIN-999"


# UPDATE-TIME RECOMPUTE


@pg_only
class TestVatUpdate:
    def test_toggling_vat_on_recomputes_tax(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id, vat_enabled=False))
        assert po.tax_total == Decimal("0.00")

        svc.update(
            po.id,
            PurchaseOrderUpdate(vat_enabled=True, vat_rate=VAT_16),
            expected_version=1,
        )
        assert po.tax_total == Decimal("32.00")
        assert po.total == Decimal("232.00")
        assert po.version == 2

    def test_toggling_vat_off_clears_rate_and_tax(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, vat_enabled=True, vat_rate=VAT_16)
        )
        svc.update(po.id, PurchaseOrderUpdate(vat_enabled=False), expected_version=1)
        assert po.tax_total == Decimal("0.00")
        assert po.total == Decimal("200.00")
        assert po.vat_rate is None

    def test_rate_change_recomputes(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, vat_enabled=True, vat_rate=VAT_16)
        )
        svc.update(
            po.id,
            PurchaseOrderUpdate(vat_rate=Decimal("0.08")),
            expected_version=1,
        )
        assert po.tax_total == Decimal("16.00")
        assert po.total == Decimal("216.00")

    def test_disabling_vat_below_amount_paid_is_blocked(self, db):
        # The amount-paid floor guard must hold when a VAT change would drop
        # the total below what has already been paid.
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, vat_enabled=True, vat_rate=VAT_16)
        )
        po.status = PurchaseOrderStatus.SENT
        db.flush()
        # Pay the full VAT-inclusive total (232), then try to remove VAT which
        # would reduce the total to 200 < 232 paid.
        svc.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("232.00"),
                paymentDate=date.today(),
                reference="TXN-VAT",
                currency="KES",
                exchangeRate=Decimal("1"),
            ),
        )
        db.flush()
        po.status = PurchaseOrderStatus.DRAFT  # re-open to allow edit attempt
        db.flush()
        with pytest.raises(BadRequestException):
            svc.update(
                po.id,
                PurchaseOrderUpdate(vat_enabled=False),
                expected_version=po.version,
            )


# OVERPAYMENT WITH VAT


@pg_only
class TestOverpaidWithVat:
    def test_overpayment_drives_balance_negative(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, vat_enabled=True, vat_rate=VAT_16)
        )
        po.status = PurchaseOrderStatus.SENT
        db.flush()
        svc.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("250.00"),
                paymentDate=date.today(),
                reference="TXN-OVER",
                currency="KES",
                exchangeRate=Decimal("1"),
            ),
        )
        db.flush()
        # total 232, paid 250 -> balance -18 (credit owed back), settled PAID.
        assert po.amount_paid == Decimal("250.00")
        assert po.balance_due == Decimal("-18.00")
        assert po.status == PurchaseOrderStatus.PAID


# PREVIEW / PERSIST PARITY


@pg_only
class TestPreviewPersistParity:
    def test_calculate_matches_create(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        items = [
            _line(quantity=Decimal("2"), unit_price=Decimal("100.00")),
            _line(quantity=Decimal("7"), unit_price=Decimal("12.34")),
        ]

        preview = PurchaseOrderService.calculate_totals(
            items, vat_enabled=True, vat_rate=VAT_16
        )
        po = svc.create(
            _create_payload(
                vendor_id=vendor.id,
                line_items=items,
                vat_enabled=True,
                vat_rate=VAT_16,
            )
        )

        assert preview.subtotal == po.subtotal
        assert preview.tax_total == po.tax_total
        assert preview.total == po.total
