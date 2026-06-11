"""Tests for the quotes consistency fixes in #34.

Covers:
- ISSUE-001: QuoteService.update uses the atomic assert_version guard, so a
  stale-version writer is rejected rather than silently winning.
- ISSUE-059: a SENT quote cannot be converted to an invoice; it must be
  APPROVED first, so the SENT -> INVOICED edge is gone from the state machine.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException, ConflictException
from app.constants.enums import Currency, CustomerStatus, QuoteStatus
from app.modules.customers.models import Customer
from app.modules.quotes.models import Quote, QuoteLineItem
from app.modules.quotes.schemas import QuoteUpdate
from app.modules.quotes.service import QuoteService


def _customer(db, email="quote-fix@acme.test") -> Customer:
    c = Customer(
        customer_type="business",
        company_name="Acme",
        first_name="Ada",
        last_name="L",
        email=email,
        phone="+254700000000",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(c)
    db.flush()
    return c


def _quote(db, customer, *, status=QuoteStatus.DRAFT, number="QTE-FIX-1") -> Quote:
    today = date.today()
    q = Quote(
        quote_number=number,
        quote_reference=number.replace("QTE", "QT"),
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=status,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("100.00"),
        version=1,
    )
    db.add(q)
    db.flush()
    db.add(
        QuoteLineItem(
            quote_id=q.id,
            line_number=1,
            item_name="Widget",
            description="A widget",
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
            tax_type="no_tax",
            tax_amount=Decimal("0.00"),
        )
    )
    db.flush()
    return q


class TestQuoteOptimisticLock:
    """ISSUE-001: stale version must be rejected by assert_version."""

    def test_stale_version_conflicts(self, db):
        customer = _customer(db)
        quote = _quote(db, customer)
        service = QuoteService(db)

        service.update(quote.id, QuoteUpdate(notes="first"), expected_version=1)
        assert quote.version == 2

        with pytest.raises(ConflictException):
            service.update(quote.id, QuoteUpdate(notes="stale"), expected_version=1)

    def test_matching_version_succeeds(self, db):
        customer = _customer(db, email="quote-ok@acme.test")
        quote = _quote(db, customer, number="QTE-FIX-OK")
        service = QuoteService(db)

        service.update(quote.id, QuoteUpdate(notes="v1"), expected_version=1)
        service.update(
            quote.id, QuoteUpdate(notes="v2"), expected_version=quote.version
        )
        assert quote.version == 3


class TestQuoteApprovalGate:
    """ISSUE-059: conversion requires APPROVED, never plain SENT."""

    def test_sent_quote_cannot_convert(self, db):
        customer = _customer(db, email="quote-sent@acme.test")
        quote = _quote(db, customer, status=QuoteStatus.SENT, number="QTE-SENT-1")
        assert quote.can_convert_to_invoice is False

        service = QuoteService(db)
        with pytest.raises(BadRequestException):
            service.convert_to_invoice(quote.id)

    def test_approved_quote_can_convert(self, db):
        customer = _customer(db, email="quote-appr@acme.test")
        quote = _quote(db, customer, status=QuoteStatus.APPROVED, number="QTE-APPR-1")
        assert quote.can_convert_to_invoice is True

    def test_state_machine_has_no_sent_to_invoiced_edge(self):
        # SENT must be approved first; the direct edge is removed.
        assert (
            QuoteStatus.INVOICED
            not in QuoteService.ALLOWED_TRANSITIONS[QuoteStatus.SENT]
        )
        assert (
            QuoteStatus.INVOICED
            in QuoteService.ALLOWED_TRANSITIONS[QuoteStatus.APPROVED]
        )
