"""
Purchase Order CRUD + reference generation + optimistic locking.


- Reference generation: po_number date-scoped, po_reference monotonic
  PO-NNNNNN, never reused after hard delete.
- Create validations: vendor required/exists/active, >=1 line item,
  delivery_date >= order_date, qty>0/price>=0, totals via shared financial.
- Currency lock: rejected if changed after first save.
- Editable only in DRAFT.
- Optimistic locking: stale version -> 409; matching -> success.
- DELETE allowed only on DRAFT; writes a before-image audit row.
- terms_and_conditions <= 2000 chars.
- No N+1: get_by_id eager-loads line_items + vendor.

Pure-logic tests run everywhere; the FOR UPDATE row-lock and advisory-lock
paths are Postgres-only and are guarded with skipif(not USING_POSTGRES).
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect

from app.common.audit import AuditEvent
from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.constants.enums import Currency, PurchaseOrderStatus, TaxType, VendorStatus
from app.modules.purchase_orders.models import PurchaseOrder, PurchaseOrderLineItem
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderUpdate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES, TestingSessionLocal

# FIXTURES / HELPERS


def _vendor(
    db,
    *,
    name="Acme Supplies",
    currency=Currency.KES,
    status=VendorStatus.ACTIVE,
    email=None,
) -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        currency=currency,
        status=status,
        email=email,
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


def _create_payload(**kw) -> PurchaseOrderCreate:
    base = {
        "order_date": date.today(),
        "line_items": [_line_item()],
    }
    base.update(kw)
    return PurchaseOrderCreate(**base)


# CREATE + VALIDATIONS


class TestCreate:
    def test_create_draft_with_totals(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)

        po = svc.create(_create_payload(vendor_id=vendor.id))

        assert po.status == PurchaseOrderStatus.DRAFT
        assert po.version == 1
        # 2 x 100 = 200 subtotal; tax 200 x 0.16 = 32; total 232.
        assert po.subtotal == Decimal("200.00")
        assert po.tax_total == Decimal("32.00")
        assert po.total == Decimal("232.00")
        assert len(po.line_items) == 1
        # Currency pinned to the vendor's currency.
        assert po.currency == Currency.KES

    def test_create_unknown_vendor_raises_not_found(self, db):
        import uuid

        svc = PurchaseOrderService(db)
        with pytest.raises(NotFoundException):
            svc.create(_create_payload(vendor_id=uuid.uuid4()))

    def test_create_inactive_vendor_rejected(self, db):
        vendor = _vendor(db, status=VendorStatus.INACTIVE, name="Dormant Co")
        svc = PurchaseOrderService(db)
        with pytest.raises(BadRequestException):
            svc.create(_create_payload(vendor_id=vendor.id))

    def test_create_requires_at_least_one_line_item(self):
        # Schema-level guard (min_length=1).
        with pytest.raises(ValidationError):
            PurchaseOrderCreate(order_date=date.today(), line_items=[])

    def test_delivery_date_before_order_date_rejected(self):
        with pytest.raises(ValidationError):
            _create_payload(
                order_date=date(2026, 6, 16),
                delivery_date=date(2026, 6, 15),
            )

    def test_quantity_must_be_positive(self):
        with pytest.raises(ValidationError):
            _line_item(quantity=Decimal("0"))

    def test_unit_price_non_negative(self):
        with pytest.raises(ValidationError):
            _line_item(unit_price=Decimal("-1.00"))

    def test_zero_rated_tax_allowed(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(
                vendor_id=vendor.id,
                line_items=[_line_item(tax_type=TaxType.VAT_0)],
            )
        )
        assert po.tax_total == Decimal("0.00")


# REFERENCE GENERATION


class TestReferenceGeneration:
    def test_reference_formats(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))

        day = date.today().strftime("%Y%m%d")
        assert po.po_number.startswith(f"PO-{day}-")
        # po_reference is the monotonic PO-NNNNNN form (6-wide suffix).
        assert po.po_reference.startswith("PO-")
        suffix = po.po_reference.split("-")[1]
        assert len(suffix) == 6 and suffix.isdigit()

    @pytest.mark.skipif(
        not USING_POSTGRES,
        reason="MAX strategy + advisory lock are Postgres constructs.",
    )
    def test_reference_not_reused_after_hard_delete(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)

        first = svc.create(_create_payload(vendor_id=vendor.id))
        second = svc.create(_create_payload(vendor_id=vendor.id))
        first_ref, second_ref = first.po_reference, second.po_reference
        assert first_ref != second_ref

        # Hard-delete the most recent PO, then create again: the suffix must
        # advance, never reuse the freed number.
        svc.delete(second.id)
        db.flush()

        third = svc.create(_create_payload(vendor_id=vendor.id))
        assert third.po_reference not in {first_ref, second_ref}


# CURRENCY LOCK


class TestCurrencyLock:
    def test_explicit_currency_is_ignored_and_pinned_to_vendor(self, db):
        # Currency is derived from the vendor server-side: an explicit
        # (mismatching) currency on the create payload is ignored, not
        # rejected, and the PO is pinned to the vendor's currency.
        vendor = _vendor(db, currency=Currency.KES)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id, currency=Currency.USD))
        assert po.currency == Currency.KES

    def test_currency_absent_from_update_schema(self):
        # currency is locked: the update schema must not even accept it.
        assert "currency" not in PurchaseOrderUpdate.model_fields

    def test_currency_unchanged_through_update(self, db):
        vendor = _vendor(db, currency=Currency.KES)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))
        svc.update(po.id, PurchaseOrderUpdate(notes="edit"), expected_version=1)
        assert po.currency == Currency.KES


# EDIT GATE + OPTIMISTIC LOCKING


class TestUpdate:
    def test_edit_blocked_unless_draft(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))

        # Force a non-DRAFT status directly (bypassing the send path).
        po.status = PurchaseOrderStatus.SENT
        db.flush()

        with pytest.raises(BadRequestException):
            svc.update(po.id, PurchaseOrderUpdate(notes="nope"), expected_version=None)

    def test_line_item_replace_recomputes_totals(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))

        svc.update(
            po.id,
            PurchaseOrderUpdate(
                line_items=[
                    _line_item(quantity=Decimal("1"), unit_price=Decimal("50.00"))
                ]
            ),
            expected_version=1,
        )
        assert po.subtotal == Decimal("50.00")
        assert po.tax_total == Decimal("8.00")
        assert po.total == Decimal("58.00")
        assert po.version == 2

    def test_stale_version_conflicts(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))

        svc.update(po.id, PurchaseOrderUpdate(notes="first"), expected_version=1)
        assert po.version == 2

        with pytest.raises(ConflictException):
            svc.update(po.id, PurchaseOrderUpdate(notes="stale"), expected_version=1)

    def test_matching_version_succeeds(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))

        svc.update(po.id, PurchaseOrderUpdate(notes="v1"), expected_version=1)
        svc.update(po.id, PurchaseOrderUpdate(notes="v2"), expected_version=po.version)
        assert po.version == 3

    def test_mixed_date_validation_against_persisted(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, order_date=date(2026, 6, 16))
        )
        # Supply only delivery_date earlier than the persisted order_date.
        with pytest.raises(BadRequestException):
            svc.update(
                po.id,
                PurchaseOrderUpdate(delivery_date=date(2026, 6, 15)),
                expected_version=1,
            )


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="True concurrency needs FOR UPDATE row locks (PostgreSQL).",
)
class TestTrueConcurrency:
    def test_two_updates_exactly_one_wins(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))
        db.commit()  # make the row visible to other sessions

        po_id = po.id
        session_a = TestingSessionLocal()
        session_b = TestingSessionLocal()
        try:
            svc_a = PurchaseOrderService(session_a)
            svc_b = PurchaseOrderService(session_b)

            svc_a.update(po_id, PurchaseOrderUpdate(notes="A"), expected_version=1)
            session_a.commit()

            with pytest.raises(ConflictException):
                svc_b.update(po_id, PurchaseOrderUpdate(notes="B"), expected_version=1)
                session_b.commit()
            session_b.rollback()
        finally:
            session_a.close()
            session_b.close()
            db.query(PurchaseOrderLineItem).filter(
                PurchaseOrderLineItem.po_id == po_id
            ).delete()
            db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).delete()
            db.query(Vendor).filter(Vendor.id == vendor.id).delete()
            db.commit()


# DELETE


class TestDelete:
    def test_delete_draft_writes_audit_before_image(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))
        po_id = po.id

        soft = svc.delete(po_id)
        assert soft is False  # hard delete in v1

        assert db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() is None

        audit = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "purchase_order",
                AuditEvent.entity_id == po_id,
                AuditEvent.action == "hard_deleted",
            )
            .first()
        )
        assert audit is not None
        assert audit.before["status"] == PurchaseOrderStatus.DRAFT.value
        assert audit.before["po_reference"] == po.po_reference

    def test_delete_sent_rejected(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))
        po.status = PurchaseOrderStatus.SENT
        db.flush()

        with pytest.raises(BadRequestException):
            svc.delete(po.id)

    def test_delete_paid_rejected(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(_create_payload(vendor_id=vendor.id))
        po.status = PurchaseOrderStatus.PAID
        db.flush()

        with pytest.raises(BadRequestException):
            svc.delete(po.id)


# SCHEMA INVARIANTS + READ EAGER-LOADING


class TestSchemaAndReads:
    def test_terms_and_conditions_max_2000(self):
        with pytest.raises(ValidationError):
            _create_payload(terms_and_conditions="x" * 2001)

    def test_terms_and_conditions_2000_ok(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        po = svc.create(
            _create_payload(vendor_id=vendor.id, terms_and_conditions="x" * 2000)
        )
        assert len(po.terms_and_conditions) == 2000

    def test_get_by_id_eager_loads_relations(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        created = svc.create(_create_payload(vendor_id=vendor.id))
        db.expire_all()  # force a clean load so eager options are exercised

        po = svc.get_by_id(created.id)
        state = sa_inspect(po)
        # No N+1: vendor and line_items are already loaded, not deferred.
        assert "vendor" not in state.unloaded
        assert "line_items" not in state.unloaded

    def test_get_by_number_round_trips(self, db):
        vendor = _vendor(db)
        svc = PurchaseOrderService(db)
        created = svc.create(_create_payload(vendor_id=vendor.id))
        fetched = svc.get_by_number(created.po_number)
        assert fetched.id == created.id

    def test_get_unknown_raises_not_found(self, db):
        import uuid

        svc = PurchaseOrderService(db)
        with pytest.raises(NotFoundException):
            svc.get_by_id(uuid.uuid4())
