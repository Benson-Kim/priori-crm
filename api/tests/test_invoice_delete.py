"""
Tests for invoice deletion.

Validates that invoice deletion follows the same patterns as purchase orders:
- Row locking to prevent race conditions
- Status validation (DRAFT only)
- Document storage cleanup
- Audit trail
- Event emission
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.common.audit import AuditEvent
from app.common.exceptions import BadRequestException, NotFoundException
from app.constants.enums import Currency, CustomerType, InvoiceStatus
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.invoices.service import InvoiceService


@pytest.fixture
def owner(db):
    """Create a test owner."""
    from app.modules.auth.models import User

    owner = User(
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="dummy_hash",
        first_name="Test",
        last_name="Owner",
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return owner


@pytest.fixture
def sample_customer(db, owner):
    """Create a test customer."""
    from app.modules.customers.models import Customer

    customer = Customer(
        customer_type=CustomerType.BUSINESS,
        company_name="Test Company Ltd",
        first_name="Test",
        last_name="Customer",
        email=f"customer-{uuid.uuid4().hex[:8]}@example.com",
        phone="+254712345678",
        currency=Currency.KES,
        is_active=True,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@pytest.fixture
def invoice_service(db, owner):
    """Create an InvoiceService instance for testing."""
    return InvoiceService(db=db, current_user=owner)


@pytest.fixture
def sample_invoice(db, owner, sample_customer):
    """Factory fixture to create test invoices."""

    def _create_invoice(status=InvoiceStatus.DRAFT):
        invoice = Invoice(
            customer_id=sample_customer.id,
            status=status,
            invoice_number=f"INV-TEST-{uuid.uuid4().hex[:8]}",
            invoice_reference=f"REF-{uuid.uuid4().hex[:8]}",
            transaction_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_total=Decimal("0.00"),
            total_due=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("100.00"),
            created_by=owner.id,
            version=1,
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        return invoice

    return _create_invoice


class TestInvoiceDelete:
    """Test suite for invoice deletion."""

    def test_delete_draft_succeeds(self, invoice_service, sample_invoice):
        """Draft invoices can be deleted."""
        # Arrange: Create a draft invoice
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)
        invoice_id = invoice.id

        # Act: Delete the invoice
        result = invoice_service.delete_invoice(invoice_id)

        # Assert: Returns False (hard delete) and invoice is gone
        assert result is False
        with pytest.raises(NotFoundException):
            invoice_service.get_by_id(invoice_id)

    def test_delete_sent_rejected(self, invoice_service, sample_invoice):
        """Sent invoices cannot be deleted."""
        # Arrange: Create a sent invoice
        invoice = sample_invoice(status=InvoiceStatus.SENT)

        # Act & Assert: Deletion is rejected
        with pytest.raises(BadRequestException) as exc_info:
            invoice_service.delete_invoice(invoice.id)

        assert "Cannot delete" in str(exc_info.value)
        assert "SENT" in str(exc_info.value) or "sent" in str(exc_info.value)
        assert "permanent record" in str(exc_info.value)

    def test_delete_paid_rejected(self, invoice_service, sample_invoice):
        """Paid invoices cannot be deleted."""
        # Arrange: Create a paid invoice
        invoice = sample_invoice(status=InvoiceStatus.PAID)

        # Act & Assert: Deletion is rejected
        with pytest.raises(BadRequestException) as exc_info:
            invoice_service.delete_invoice(invoice.id)

        assert "Cannot delete" in str(exc_info.value)
        assert "PAID" in str(exc_info.value) or "paid" in str(exc_info.value)

    def test_delete_partial_rejected(self, invoice_service, sample_invoice):
        """Partially paid invoices cannot be deleted."""
        # Arrange: Create a partially paid invoice
        invoice = sample_invoice(status=InvoiceStatus.PARTIAL)

        # Act & Assert: Deletion is rejected
        with pytest.raises(BadRequestException) as exc_info:
            invoice_service.delete_invoice(invoice.id)

        assert "Cannot delete" in str(exc_info.value)

    def test_delete_overdue_rejected(self, invoice_service, sample_invoice):
        """Overdue invoices cannot be deleted."""
        # Arrange: Create an overdue invoice
        invoice = sample_invoice(status=InvoiceStatus.OVERDUE)

        # Act & Assert: Deletion is rejected
        with pytest.raises(BadRequestException) as exc_info:
            invoice_service.delete_invoice(invoice.id)

        assert "Cannot delete" in str(exc_info.value)

    def test_delete_canceled_rejected(self, invoice_service, sample_invoice):
        """Canceled invoices cannot be deleted."""
        # Arrange: Create a canceled invoice
        invoice = sample_invoice(status=InvoiceStatus.CANCELED)

        # Act & Assert: Deletion is rejected
        with pytest.raises(BadRequestException) as exc_info:
            invoice_service.delete_invoice(invoice.id)

        assert "Cannot delete" in str(exc_info.value)

    def test_delete_not_found(self, invoice_service):
        """Deleting non-existent invoice raises NotFoundException."""
        # Arrange: Random UUID that doesn't exist
        fake_id = uuid.uuid4()

        # Act & Assert: Not found
        with pytest.raises(NotFoundException):
            invoice_service.delete_invoice(fake_id)

    def test_delete_writes_audit_before_image(
        self, invoice_service, sample_invoice, db
    ):
        """Deletion writes audit trail with before-image."""
        # Arrange: Create a draft invoice
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)
        invoice_id = invoice.id
        invoice_number = invoice.invoice_number
        invoice_reference = invoice.invoice_reference

        # Act: Delete the invoice
        invoice_service.delete_invoice(invoice_id)

        # Assert: Audit event was written
        audit = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "invoice",
                AuditEvent.entity_id == invoice_id,
                AuditEvent.action == "hard_deleted",
            )
            .first()
        )

        assert audit is not None
        assert audit.before["invoice_number"] == invoice_number
        assert audit.before["invoice_reference"] == invoice_reference
        assert audit.before["status"] == "draft"
        assert audit.after is None

    def test_delete_emits_event(self, invoice_service, sample_invoice):
        """Deletion emits analytics event."""
        # Arrange: Create a draft invoice
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)
        invoice_id = invoice.id

        # Act: Delete with event capture
        with patch("app.common.analytics.emit_event") as mock_emit:
            invoice_service.delete_invoice(invoice_id)

        # Assert: Event was emitted
        mock_emit.assert_called_once()
        call_args = mock_emit.call_args
        assert "invoice_deleted" in str(call_args[0][0]).lower()
        assert str(invoice_id) in str(call_args[0][1])

    def test_delete_acquires_row_lock(self, invoice_service, sample_invoice, db):
        """Deletion uses SELECT FOR UPDATE to prevent races."""
        # Arrange: Create a draft invoice
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)

        # Act: Mock the query chain to verify with_for_update is called
        with patch.object(db, "query") as mock_query:
            mock_chain = MagicMock()
            mock_query.return_value = mock_chain
            mock_chain.filter.return_value = mock_chain
            mock_chain.with_for_update.return_value = mock_chain
            mock_chain.first.return_value = invoice

            from contextlib import suppress

            with suppress(Exception):
                invoice_service.delete_invoice(invoice.id)

        # Assert: with_for_update was called
        mock_chain.with_for_update.assert_called_once()

    def test_delete_purges_document_storage(self, invoice_service, sample_invoice, db):
        """Deletion purges document storage objects."""
        # Arrange: Create invoice and mock documents attribute
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)

        # Mock the documents relationship with storage keys
        mock_doc = MagicMock()
        mock_doc.storage_key = "test-storage-key-123"

        # Patch the invoice to have documents
        with patch.object(type(invoice), "documents", [mock_doc], create=True):
            # Act: Delete with storage purge mock
            with patch.object(invoice_service, "_purge_storage_objects") as mock_purge:
                invoice_service.delete_invoice(invoice.id)

            # Assert: Storage purge was called with correct keys
            mock_purge.assert_called_once()
            called_keys = mock_purge.call_args[0][0]
            assert "test-storage-key-123" in called_keys

    def test_delete_handles_database_error(self, invoice_service, sample_invoice, db):
        """Database errors are handled gracefully."""
        # Arrange: Create a draft invoice
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)

        # Act: Force a database error during delete
        with (
            patch.object(db, "delete", side_effect=SQLAlchemyError("DB error")),
            pytest.raises(Exception) as exc_info,
        ):
            invoice_service.delete_invoice(invoice.id)

        # Assert: Error is raised
        assert "DB error" in str(exc_info.value) or "Failed to delete" in str(
            exc_info.value
        )

    def test_delete_cascades_line_items(self, invoice_service, sample_invoice, db):
        """Deletion cascades to line items."""
        # Arrange: Create invoice with line items
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)

        line_item = InvoiceLineItem(
            invoice_id=invoice.id,
            line_number=1,
            item_name="Test Item",
            description="Test Description",
            quantity=Decimal("2.00"),
            unit_price=Decimal("50.00"),
            line_total=Decimal("100.00"),
        )
        db.add(line_item)
        db.commit()

        # Act: Delete the invoice
        invoice_service.delete_invoice(invoice.id)

        # Assert: Line items are also deleted
        remaining_items = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == invoice.id)
            .all()
        )
        assert len(remaining_items) == 0

    def test_delete_with_payments_rejected(self, invoice_service, sample_invoice, db):
        """Invoices with payments cannot be deleted (should be PARTIAL or PAID status)."""
        # Arrange: Create a draft invoice (though in reality, having payments
        # would transition it to PARTIAL/PAID)
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)

        # Add a payment
        from app.modules.invoices.models import Payment

        payment = Payment(
            invoice_id=invoice.id,
            amount=Decimal("50.00"),
            payment_date=date.today(),
            payment_method="bank_transfer",
        )
        db.add(payment)
        db.commit()

        # In a real scenario, this would transition to PARTIAL status
        # For this test, we manually set it to PARTIAL to reflect reality
        invoice.status = InvoiceStatus.PARTIAL
        db.commit()

        # Act & Assert: Deletion is rejected
        with pytest.raises(BadRequestException):
            invoice_service.delete_invoice(invoice.id)

    def test_delete_returns_false_for_hard_delete(
        self, invoice_service, sample_invoice
    ):
        """Deletion returns False to indicate hard delete (no soft-delete)."""
        # Arrange: Create a draft invoice
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)

        # Act: Delete the invoice
        result = invoice_service.delete_invoice(invoice.id)

        # Assert: Returns False (hard delete, not soft delete)
        assert result is False

    def test_delete_logs_operation(self, invoice_service, sample_invoice):
        """Deletion logs the operation."""
        # Arrange: Create a draft invoice
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)
        invoice_number = invoice.invoice_number

        # Act: Delete with logging capture
        with patch("app.modules.invoices.service.logger") as mock_logger:
            invoice_service.delete_invoice(invoice.id)

        # Assert: Warning or info was logged
        assert mock_logger.warning.called or mock_logger.info.called
        # Check that invoice number appears in one of the log calls
        log_calls = str(mock_logger.warning.call_args_list) + str(
            mock_logger.info.call_args_list
        )
        assert invoice_number in log_calls or str(invoice.id) in log_calls

    def test_delete_storage_cleanup_failure_does_not_fail_operation(
        self, invoice_service, sample_invoice, db
    ):
        """Storage cleanup failures don't fail the deletion operation."""
        # Arrange: Create invoice and mock documents
        invoice = sample_invoice(status=InvoiceStatus.DRAFT)

        # Mock the documents relationship
        mock_doc = MagicMock()
        mock_doc.storage_key = "test-key"

        # Patch the invoice to have documents
        with (
            patch.object(type(invoice), "documents", [mock_doc], create=True),
            patch.object(
                invoice_service,
                "_purge_storage_objects",
                side_effect=Exception("Storage error"),
            ),
        ):
            # Should not raise - storage cleanup is best-effort
            result = invoice_service.delete_invoice(invoice.id)

        # Assert: Deletion succeeded despite storage failure
        assert result is False
        with pytest.raises(NotFoundException):
            invoice_service.get_by_id(invoice.id)


# Made with Bob
