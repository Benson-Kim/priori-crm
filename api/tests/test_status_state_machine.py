"""Status changes routed through the state machine + payment locking.

Schema-level tests run anywhere. Service-level tests use the shared `db`
fixture (PostgreSQL in CI, SQLite locally).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import (
    Currency,
    CustomerStatus,
    CustomerType,
    ExpenseStatus,
    InvoiceStatus,
    VendorStatus,
)
from app.modules.customers.schemas import CustomerUpdate
from app.modules.vendors.schemas import VendorUpdate

# schema: status is not a PUT-editable field


class TestUpdateSchemasRejectStatus:
    def test_vendor_update_ignores_status(self):
        model = VendorUpdate.model_validate(
            {"vendorName": "Acme", "status": "inactive"}
        )
        # status must not be a settable field on the update schema
        assert "status" not in model.model_dump(exclude_unset=True)
        assert not hasattr(model, "status")

    def test_customer_update_ignores_status(self):
        model = CustomerUpdate.model_validate(
            {"firstName": "Jane", "status": "deleted"}
        )
        assert "status" not in model.model_dump(exclude_unset=True)
        assert not hasattr(model, "status")


# service fixtures


def _make_customer(db):
    from app.modules.customers.models import Customer

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


def _make_vendor(db):
    from app.modules.vendors.models import Vendor

    vendor = Vendor(
        vendor_name="Test Vendor",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


# invoices: cancel via state machine + update balance clamp


class TestInvoiceStateMachine:
    def _make_invoice(self, db, status=InvoiceStatus.DRAFT, amount_paid="0.00"):
        from app.modules.invoices.models import Invoice

        customer = _make_customer(db)
        total = Decimal("100.00")
        invoice = Invoice(
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            invoice_reference=f"IN-{uuid.uuid4().hex[:6]}",
            customer_id=customer.id,
            transaction_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency=Currency.KES,
            status=status,
            subtotal=total,
            tax_total=Decimal("0.00"),
            total_due=total,
            amount_paid=Decimal(amount_paid),
            balance_due=total - Decimal(amount_paid),
            version=1,
        )
        db.add(invoice)
        db.flush()
        return invoice

    def test_cancel_invoice_uses_transition_and_bumps_version(self, db):
        from app.modules.invoices.service import InvoiceService

        invoice = self._make_invoice(db, status=InvoiceStatus.DRAFT)
        v0 = invoice.version
        service = InvoiceService(db)

        result = service.cancel_invoice(invoice.id)

        assert result.status == InvoiceStatus.CANCELED
        assert result.version == v0 + 1

    def test_cancel_already_canceled_rejected(self, db):
        from app.modules.invoices.service import InvoiceService

        invoice = self._make_invoice(db, status=InvoiceStatus.CANCELED)
        service = InvoiceService(db)

        with pytest.raises(BadRequestException):
            service.cancel_invoice(invoice.id)

    def test_update_cannot_drive_balance_negative(self, db):
        """A SENT invoice carrying a payment cannot be edited below amount_paid."""
        from app.modules.invoices.models import Invoice
        from app.modules.invoices.schemas import InvoiceLineItemCreate, InvoiceUpdate
        from app.modules.invoices.service import InvoiceService

        # Construct a SENT invoice that already has amount_paid recorded.
        customer = _make_customer(db)
        invoice = Invoice(
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            invoice_reference=f"IN-{uuid.uuid4().hex[:6]}",
            customer_id=customer.id,
            transaction_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency=Currency.KES,
            status=InvoiceStatus.SENT,
            subtotal=Decimal("1000.00"),
            tax_total=Decimal("0.00"),
            total_due=Decimal("1000.00"),
            amount_paid=Decimal("800.00"),
            balance_due=Decimal("200.00"),
            version=1,
        )
        db.add(invoice)
        db.flush()

        service = InvoiceService(db)
        # Try to shrink the total to 100 (< 800 already paid).
        payload = InvoiceUpdate(
            line_items=[
                InvoiceLineItemCreate(
                    itemName="Cheap",
                    description="x",
                    quantity=Decimal("1"),
                    unitPrice=Decimal("100.00"),
                    tax_type="no_tax",
                )
            ]
        )

        with pytest.raises(BadRequestException):
            service.update(invoice.id, payload, expected_version=invoice.version)


# expenses: unified payment settle path


class TestExpensePaymentSettlement:
    def _make_expense(self, db, total="100.00"):
        from app.modules.expenses.models import Expense

        vendor = _make_vendor(db)
        total_d = Decimal(total)
        expense = Expense(
            expense_number=f"EXP-{uuid.uuid4().hex[:8]}",
            expense_reference=f"EXP-{uuid.uuid4().hex[:6]}",
            vendor_id=vendor.id,
            expense_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency=Currency.KES,
            status=ExpenseStatus.PENDING,
            subtotal=total_d,
            tax_total=Decimal("0.00"),
            total_due=total_d,
            amount_paid=Decimal("0.00"),
            balance_due=total_d,
            version=1,
        )
        db.add(expense)
        db.flush()
        return expense

    def test_full_payment_settles_with_single_version_bump(self, db):
        from app.modules.expenses.schemas import ExpensePaymentCreate
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, total="100.00")
        v0 = expense.version
        service = ExpenseService(db)

        service.record_payment(
            expense.id,
            ExpensePaymentCreate(amount=Decimal("100.00"), paymentDate=date.today()),
        )

        db.refresh(expense)
        assert expense.status == ExpenseStatus.PAID
        assert expense.balance_due == Decimal("0.00")
        assert expense.paid_at is not None
        assert expense.version == v0 + 1  # exactly one bump

    def test_partial_payment_leaves_status_and_bumps_once(self, db):
        from app.modules.expenses.schemas import ExpensePaymentCreate
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, total="100.00")
        v0 = expense.version
        service = ExpenseService(db)

        service.record_payment(
            expense.id,
            ExpensePaymentCreate(amount=Decimal("40.00"), paymentDate=date.today()),
        )

        db.refresh(expense)
        assert expense.status == ExpenseStatus.PENDING
        assert expense.balance_due == Decimal("60.00")
        assert expense.version == v0 + 1

    def test_mark_as_paid_settles_via_shared_path(self, db):
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, total="250.00")
        v0 = expense.version
        service = ExpenseService(db)

        service.mark_as_paid(expense.id)

        db.refresh(expense)
        assert expense.status == ExpenseStatus.PAID
        assert expense.amount_paid == Decimal("250.00")
        assert expense.balance_due == Decimal("0.00")
        assert expense.version == v0 + 1

    def test_overpay_accepted(self, db):
        from app.modules.expenses.schemas import ExpensePaymentCreate
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, total="100.00")
        service = ExpenseService(db)

        service.record_payment(
            expense.id,
            ExpensePaymentCreate(amount=Decimal("150.00"), paymentDate=date.today()),
        )

        db.refresh(expense)
        assert expense.status == ExpenseStatus.PAID
        assert expense.amount_paid == Decimal("150.00")
        assert expense.balance_due == Decimal("-50.00")

    def test_payment_on_paid_expense_accepted(self, db):
        from app.modules.expenses.schemas import ExpensePaymentCreate
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, total="100.00")
        service = ExpenseService(db)
        service.record_payment(
            expense.id,
            ExpensePaymentCreate(amount=Decimal("100.00"), paymentDate=date.today()),
        )

        # A further payment on a settled expense is accepted and drives the
        # balance negative (credit owed back).
        service.record_payment(
            expense.id,
            ExpensePaymentCreate(amount=Decimal("10.00"), paymentDate=date.today()),
        )

        db.refresh(expense)
        assert expense.status == ExpenseStatus.PAID
        assert expense.amount_paid == Decimal("110.00")
        assert expense.balance_due == Decimal("-10.00")


class TestExpenseCanceledFirstClass:
    """CANCELED is a first-class terminal expense state."""

    def _make_expense(self, db, *, status=ExpenseStatus.PENDING, total="100.00"):
        from app.modules.expenses.models import Expense

        vendor = _make_vendor(db)
        total_d = Decimal(total)
        expense = Expense(
            expense_number=f"EXP-{uuid.uuid4().hex[:8]}",
            expense_reference=f"EXP-{uuid.uuid4().hex[:6]}",
            vendor_id=vendor.id,
            expense_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency=Currency.KES,
            status=status,
            subtotal=total_d,
            tax_total=Decimal("0.00"),
            total_due=total_d,
            amount_paid=Decimal("0.00"),
            balance_due=total_d,
            version=1,
        )
        db.add(expense)
        db.flush()
        return expense

    def test_cancel_uses_transition_and_bumps_version(self, db):
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, status=ExpenseStatus.PENDING)
        v0 = expense.version
        service = ExpenseService(db)

        result = service.cancel(expense.id)

        assert result.status == ExpenseStatus.CANCELED
        assert result.version == v0 + 1

    def test_paid_expense_can_be_voided_to_canceled(self, db):
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, status=ExpenseStatus.PAID)
        service = ExpenseService(db)

        result = service.cancel(expense.id)
        assert result.status == ExpenseStatus.CANCELED

    def test_cancel_already_canceled_rejected(self, db):
        from app.modules.expenses.service import ExpenseService

        expense = self._make_expense(db, status=ExpenseStatus.CANCELED)
        service = ExpenseService(db)

        with pytest.raises(BadRequestException):
            service.cancel(expense.id)

    def test_default_list_hides_canceled_but_filter_surfaces_it(self, db):
        from app.common.pagination import PaginationParams
        from app.modules.expenses.schemas import ExpenseFilterParams
        from app.modules.expenses.service import ExpenseService

        service = ExpenseService(db)
        live = self._make_expense(db, status=ExpenseStatus.PENDING)
        canceled = self._make_expense(db, status=ExpenseStatus.PENDING)
        service.cancel(canceled.id)

        default_ids = {
            row.id for row in service.list_expenses(PaginationParams()).items
        }
        assert live.id in default_ids
        assert canceled.id not in default_ids

        canceled_ids = {
            row.id
            for row in service.list_expenses(
                PaginationParams(),
                ExpenseFilterParams(status=ExpenseStatus.CANCELED),
            ).items
        }
        assert canceled.id in canceled_ids
        assert live.id not in canceled_ids

    def test_status_counts_track_canceled_separately(self, db):
        from app.modules.expenses.service import ExpenseService

        service = ExpenseService(db)
        # Two PENDING expenses; cancel one. due_date is in the future so
        # neither contributes to the computed-overdue bucket.
        self._make_expense(db, status=ExpenseStatus.PENDING)
        canceled = self._make_expense(db, status=ExpenseStatus.PENDING)
        service.cancel(canceled.id)

        counts = service.get_status_counts()
        # The canceled row is surfaced on its own count and excluded from
        # `all` (which tracks live expenses only).
        assert counts.canceled == 1
        assert counts.pending == 1
        assert counts.all == 1
