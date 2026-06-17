"""org-scoped Settings defaults (T&C, send message, jurisdiction).

Acceptance criteria exercised here:
- Settings keys are stored once (org scope) on the OwnerProfile singleton, not
  per PO.
- terms_and_conditions is pre-filled from the Settings default on a new PO;
  the out-of-the-box text is used when the org never set one.
- Defaults are applied at CREATE TIME ONLY: editing a Settings default never
  alters an existing PO.
- jurisdiction is normalised and snapshotted so the issued-document compliance
  label is frozen at issue time.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.constants.enums import Currency, TaxType, VendorStatus
from app.constants.settings_defaults import (
    DEFAULT_ORG_JURISDICTION,
    DEFAULT_PURCHASE_ORDER_TERMS,
)
from app.modules.owner.schemas import OwnerProfileUpdate
from app.modules.owner.service import OwnerService
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

# Reference generation uses pg_advisory_xact_lock, so the create path is
# PostgreSQL-only. The OwnerService tests below are backend-agnostic.
pg_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="PO create uses advisory-lock reference generation (PostgreSQL only)",
)


def _vendor(db, *, currency=Currency.KES, email="v@po-settings.test") -> Vendor:
    vendor = Vendor(
        vendor_name="Acme Supplies",
        email=email,
        currency=currency,
        status=VendorStatus.ACTIVE,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line_items() -> list[PurchaseOrderLineItemCreate]:
    return [
        PurchaseOrderLineItemCreate(
            itemName="Bolts",
            description="Box of bolts",
            quantity=Decimal("1"),
            unitPrice=Decimal("100.00"),
            taxType=TaxType.NO_TAX,
        )
    ]


def _po_create(vendor_id, **overrides) -> PurchaseOrderCreate:
    payload = dict(
        vendorId=vendor_id,
        orderDate=date.today(),
        lineItems=_line_items(),
    )
    payload.update(overrides)
    return PurchaseOrderCreate(**payload)


class TestOwnerSettingsDefaults:
    """OwnerService.purchase_order_defaults() resolution."""

    def test_unset_defaults_resolve_to_builtins(self, db):
        defaults = OwnerService(db).purchase_order_defaults()

        assert defaults.terms_and_conditions == DEFAULT_PURCHASE_ORDER_TERMS
        assert defaults.jurisdiction == DEFAULT_ORG_JURISDICTION
        assert defaults.send_message is None

    def test_persisted_values_win_over_builtins(self, db):
        service = OwnerService(db)
        service.update(
            OwnerProfileUpdate(
                fullName="Priori",
                defaultTermsAndConditions="Net 30. All sales final.",
                defaultSendMessage="Please find our PO attached.",
                jurisdiction="ug",  # lower-case on input
            )
        )
        db.flush()

        defaults = service.purchase_order_defaults()

        assert defaults.terms_and_conditions == "Net 30. All sales final."
        assert defaults.send_message == "Please find our PO attached."
        # Normalised to upper-case ISO 3166-1 alpha-2.
        assert defaults.jurisdiction == "UG"

    def test_blank_jurisdiction_falls_back_to_builtin(self, db):
        service = OwnerService(db)
        service.update(OwnerProfileUpdate(fullName="Priori", jurisdiction=""))
        db.flush()

        assert (
            service.purchase_order_defaults().jurisdiction == DEFAULT_ORG_JURISDICTION
        )

    def test_invalid_jurisdiction_rejected(self):
        with pytest.raises(ValueError):
            OwnerProfileUpdate(fullName="Priori", jurisdiction="Kenya")

    def test_settings_are_org_scoped_singleton(self, db):
        """Updating settings mutates the one profile row, not a per-PO copy."""
        service = OwnerService(db)
        first = service.update(
            OwnerProfileUpdate(fullName="Priori", defaultSendMessage="v1")
        )
        second = service.update(
            OwnerProfileUpdate(fullName="Priori", defaultSendMessage="v2")
        )
        assert first.id == second.id
        assert second.default_send_message == "v2"


@pg_only
class TestCreateTimeDefaultApplication:
    """Default T&C is applied to a new PO at create time only."""

    def test_new_po_inherits_builtin_default_when_tc_omitted(self, db):
        vendor = _vendor(db)
        po = PurchaseOrderService(db).create(_po_create(vendor.id))

        assert po.terms_and_conditions == DEFAULT_PURCHASE_ORDER_TERMS

    def test_new_po_inherits_org_default_when_tc_omitted(self, db):
        vendor = _vendor(db)
        OwnerService(db).update(
            OwnerProfileUpdate(
                fullName="Priori", defaultTermsAndConditions="Org terms apply."
            )
        )
        db.flush()

        po = PurchaseOrderService(db).create(_po_create(vendor.id))

        assert po.terms_and_conditions == "Org terms apply."

    def test_explicit_tc_is_not_overridden_by_default(self, db):
        vendor = _vendor(db)
        OwnerService(db).update(
            OwnerProfileUpdate(
                fullName="Priori", defaultTermsAndConditions="Org terms apply."
            )
        )
        db.flush()

        po = PurchaseOrderService(db).create(
            _po_create(vendor.id, termsAndConditions="Bespoke terms for this PO.")
        )

        assert po.terms_and_conditions == "Bespoke terms for this PO."

    def test_explicit_blank_tc_is_honoured(self, db):
        """An intentionally blank T&C must not be re-filled with the default."""
        vendor = _vendor(db)
        OwnerService(db).update(
            OwnerProfileUpdate(
                fullName="Priori", defaultTermsAndConditions="Org terms apply."
            )
        )
        db.flush()

        # The schema normalises an explicit blank to None; because the field
        # WAS supplied, the create-time default must not be re-applied.
        po = PurchaseOrderService(db).create(
            _po_create(vendor.id, termsAndConditions="   ")
        )

        assert po.terms_and_conditions is None

    def test_later_settings_change_does_not_affect_existing_po(self, db):
        vendor = _vendor(db)
        service = PurchaseOrderService(db)
        po = service.create(_po_create(vendor.id))
        original_tc = po.terms_and_conditions

        OwnerService(db).update(
            OwnerProfileUpdate(
                fullName="Priori",
                defaultTermsAndConditions="Brand new org terms.",
            )
        )
        db.flush()
        db.refresh(po)

        assert po.terms_and_conditions == original_tc
        assert po.terms_and_conditions != "Brand new org terms."


class TestJurisdictionSnapshot:
    """jurisdiction is frozen on the immutable owner snapshot."""

    def test_snapshot_captures_jurisdiction(self, db):
        service = OwnerService(db)
        service.update(OwnerProfileUpdate(fullName="Priori", jurisdiction="tz"))
        db.flush()

        snapshot = service.snapshot_current()
        assert snapshot.jurisdiction == "TZ"

        # The render DTO derived from the snapshot carries the frozen value.
        info = OwnerService.to_owner_info(snapshot)
        assert info.jurisdiction == "TZ"
