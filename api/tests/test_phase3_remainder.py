"""Phase 3 remainder regression tests."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.common.audit import AuditEvent, record_audit_event
from app.common.document_service import BaseDocumentService
from app.common.exceptions import BadRequestException, ConflictException
from app.constants.enums import (
    Currency,
    CustomerStatus,
    ExpenseStatus,
    VendorStatus,
)
from app.modules.customers.models import Customer
from app.modules.customers.service import CustomerService
from app.modules.expenses.models import Expense
from app.modules.expenses.service import ExpenseService
from app.modules.invoices.schemas import InvoiceCalculationResponse
from app.modules.invoices.service import InvoiceService
from app.modules.quotes.schemas import QuoteCalculationResponse
from app.modules.quotes.service import QuoteService
from app.modules.vendors.models import Vendor
from app.modules.vendors.service import VendorService


def _vendor(db, name="P3R Supplies") -> Vendor:
    v = Vendor(
        vendor_name=name,
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(v)
    db.flush()
    return v


def _expense(db, vendor, number="EXP-P3R-1") -> Expense:
    today = date.today()
    e = Expense(
        expense_number=number,
        expense_reference=number.replace("EXP-", "EXPR-"),
        vendor_id=vendor.id,
        expense_date=today,
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=ExpenseStatus.PENDING,
        is_recurring=False,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("16.00"),
        total_due=Decimal("116.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("116.00"),
        version=1,
    )
    db.add(e)
    db.flush()
    return e


def _collision_error(marker: str) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception(f"duplicate key: {marker}"))


class TestReferenceRetryTemplate:
    def test_retries_collisions_then_succeeds(self, db):
        service = QuoteService(db)
        attempts = {"n": 0}

        def build():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _collision_error("quote_number")
            return "created"

        assert service._with_reference_retry(build, "quote") == "created"
        assert attempts["n"] == 2

    def test_non_collision_integrity_error_raises_immediately(self, db):
        service = QuoteService(db)
        attempts = {"n": 0}

        def build():
            attempts["n"] += 1
            raise _collision_error("some_other_constraint")

        with pytest.raises(ConflictException):
            service._with_reference_retry(build, "quote")
        assert attempts["n"] == 1  # no retry for non-reference violations

    def test_exhausted_budget_raises_conflict(self, db):
        service = QuoteService(db)
        attempts = {"n": 0}

        def build():
            attempts["n"] += 1
            raise _collision_error("quote_reference")

        with pytest.raises(ConflictException):
            service._with_reference_retry(build, "quote")
        assert attempts["n"] == service.MAX_REFERENCE_RETRIES + 1


class TestSharedCalculateTotals:
    @pytest.mark.no_db
    def test_single_base_implementation(self):
        assert (
            InvoiceService.calculate_totals.__func__
            is BaseDocumentService.calculate_totals.__func__
        )
        assert (
            QuoteService.calculate_totals.__func__
            is BaseDocumentService.calculate_totals.__func__
        )
        # Expenses keep their discount-free override.
        assert (
            ExpenseService.calculate_totals.__func__
            is not BaseDocumentService.calculate_totals.__func__
        )

    @pytest.mark.no_db
    def test_per_service_response_classes(self):
        inv = InvoiceService.calculate_totals([])
        qte = QuoteService.calculate_totals([])
        assert isinstance(inv, InvoiceCalculationResponse)
        assert isinstance(qte, QuoteCalculationResponse)
        assert inv.subtotal == Decimal("0.00")
        assert qte.total_due == Decimal("0.00")


class TestVendorStateMachine:
    def test_transitions_via_shared_mixin(self, db):
        vendor = _vendor(db)
        service = VendorService(db)

        service.deactivate(vendor.id)
        assert vendor.status == VendorStatus.INACTIVE
        assert vendor.version == 2  # exactly one bump, owned by _transition

        # INACTIVE -> INACTIVE is not a legal edge.
        with pytest.raises(BadRequestException):
            service.deactivate(vendor.id)

        service.activate(vendor.id)
        assert vendor.status == VendorStatus.ACTIVE
        assert vendor.version == 3


class TestAuditTrail:
    def test_record_audit_event_round_trip(self, db):
        vendor = _vendor(db, name="Audit Vendor")
        event = record_audit_event(
            db,
            actor_id=None,
            entity_type="vendor",
            entity_id=vendor.id,
            action="hard_deleted",
            before={"vendor_name": vendor.vendor_name},
        )
        stored = db.get(AuditEvent, event.id)
        assert stored.entity_type == "vendor"
        assert stored.action == "hard_deleted"
        assert stored.before == {"vendor_name": "Audit Vendor"}
        assert stored.after is None

    def test_expense_cancel_writes_audit_row(self, db):
        vendor = _vendor(db, name="Cancel Vendor")
        expense = _expense(db, vendor)
        ExpenseService(db).cancel(expense.id)

        event = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "expense",
                AuditEvent.entity_id == expense.id,
            )
            .one()
        )
        assert event.action == "canceled"
        assert event.before == {"status": ExpenseStatus.PENDING.value}
        assert event.after == {"status": ExpenseStatus.CANCELED.value}

    def test_customer_soft_delete_writes_audit_row(self, db):
        customer = Customer(
            customer_type="business",
            company_name="Audit Co",
            first_name="Ada",
            last_name="L",
            email="audit@acme.test",
            phone="+254700000000",
            currency=Currency.KES,
            status=CustomerStatus.ACTIVE,
        )
        db.add(customer)
        db.flush()

        CustomerService(db).delete(customer.id, hard_delete=False)

        event = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "customer",
                AuditEvent.entity_id == customer.id,
            )
            .one()
        )
        assert event.action == "soft_deleted"
        assert event.after == {"status": CustomerStatus.DELETED.value}
