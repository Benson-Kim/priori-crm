"""Regression tests for destructive quote cascade on customer delete.

Verifies:
- Soft-deleting a customer preserves quote history (no destructive cascade).
- Hard-deleting a customer that still references quotes is rejected by the
  DB-level FK RESTRICT on Quote.customer_id and surfaces as a clean 400.
- Soft-deleted (DELETED) customers are excluded from default reads.

The FK-RESTRICT behaviour is enforced by PostgreSQL, so the destructive-delete
assertion is only meaningful on Postgres (the CI service). Like every
schema-building test, this file is skipped when no Postgres DATABASE_URL is
configured (see conftest.pytest_collection_modifyitems).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException, NotFoundException
from app.common.pagination import PaginationParams
from app.constants.enums import (
    Currency,
    CustomerStatus,
    CustomerType,
    QuoteStatus,
    TaxType,
)
from app.modules.customers.models import Customer
from app.modules.customers.service import CustomerService
from app.modules.quotes.models import Quote, QuoteLineItem
from tests.conftest import USING_POSTGRES

requires_postgres = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="FK ON DELETE RESTRICT is only enforced by PostgreSQL (CI service).",
)


def _make_customer(db) -> Customer:
    customer = Customer(
        customer_type=CustomerType.BUSINESS,
        company_name="Acme Ltd",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@acme.test",
        phone="+254700000000",
        currency=Currency.KES,
    )
    db.add(customer)
    db.flush()
    return customer


def _make_quote(db, customer: Customer) -> Quote:
    today = date.today()
    quote = Quote(
        quote_number="QTE-20260101",
        quote_reference="QT-0101",
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        status=QuoteStatus.SENT,
        currency=Currency.KES,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("16.00"),
        total_due=Decimal("116.00"),
    )
    db.add(quote)
    db.flush()

    line = QuoteLineItem(
        quote_id=quote.id,
        line_number=1,
        item_name="Widget",
        description="A widget",
        quantity=Decimal("1.00"),
        unit_price=Decimal("100.00"),
        line_total=Decimal("100.00"),
        tax_type=TaxType.VAT_16,
        tax_amount=Decimal("16.00"),
    )
    db.add(line)
    db.flush()
    return quote


def test_soft_delete_preserves_quotes_and_line_items(db):
    """Soft delete must not cascade-destroy a customer's quotes."""
    service = CustomerService(db)
    customer = _make_customer(db)
    quote = _make_quote(db, customer)
    quote_id = quote.id

    result = service.delete(customer.id, hard_delete=False, force=False)
    db.flush()

    assert result["delete_type"] == "soft"

    surviving = db.query(Quote).filter(Quote.id == quote_id).first()
    assert surviving is not None
    assert surviving.customer_id == customer.id

    lines = db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == quote_id).all()
    assert len(lines) == 1

    refreshed = db.query(Customer).filter(Customer.id == customer.id).first()
    assert refreshed.status == CustomerStatus.DELETED


@requires_postgres
def test_hard_delete_with_quotes_is_rejected_and_preserves_history(db):
    """Hard delete must be blocked by FK RESTRICT, leaving quotes intact."""
    service = CustomerService(db)
    customer = _make_customer(db)
    quote = _make_quote(db, customer)
    quote_id = quote.id

    # force=True bypasses the application-level eligibility check, so the
    # DB-level FK RESTRICT is what must protect the quote history. The service
    # isolates the failing DELETE in a SAVEPOINT, so the session stays usable
    # afterwards (no outer rollback needed) and the fixtures persist.
    with pytest.raises(BadRequestException):
        service.delete(customer.id, hard_delete=True, force=True)

    surviving = db.query(Quote).filter(Quote.id == quote_id).first()
    assert surviving is not None

    customer_still_there = db.query(Customer).filter(Customer.id == customer.id).first()
    assert customer_still_there is not None


def test_default_reads_exclude_soft_deleted_customers(db):
    """DELETED customers must be hidden from default list/detail reads."""
    service = CustomerService(db)
    customer = _make_customer(db)
    service.delete(customer.id, hard_delete=False, force=False)
    db.flush()

    # Detail read hides the soft-deleted customer by default (404).
    with pytest.raises(NotFoundException):
        service.get_by_id(customer.id)

    # ...but the lifecycle paths can still reach it explicitly.
    assert service.get_by_id(customer.id, include_deleted=True).id == customer.id

    # Default list excludes it.
    listed = service.list_customers(PaginationParams(page=1, per_page=50))
    assert all(item.id != customer.id for item in listed.items)

    # Explicit status=deleted surfaces it.
    deleted_list = service.list_customers(
        PaginationParams(page=1, per_page=50), status="deleted"
    )
    assert any(item.id == customer.id for item in deleted_list.items)
