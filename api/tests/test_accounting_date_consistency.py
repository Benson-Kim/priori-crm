"""Organization-local accounting date regressions."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import app.common.financial as financial
import app.modules.quotes.models as quote_models
import app.modules.quotes.schemas as quote_schemas
from app.common.reporting_time import (
    clear_reporting_timezone_cache,
    reporting_date,
)
from app.constants.enums import Currency, InvoiceStatus, QuoteStatus
from app.lib.config import settings
from app.modules.invoices.models import Invoice
from app.modules.invoices.schemas import InvoiceSummary
from app.modules.quotes.models import Quote
from app.modules.quotes.schemas import QuoteSummary


def test_reporting_date_uses_nairobi_day_before_utc_midnight(monkeypatch):
    monkeypatch.setattr(settings, "REPORTING_TIMEZONE", "Africa/Nairobi")
    clear_reporting_timezone_cache()
    try:
        instant = datetime(2026, 7, 21, 21, 30, tzinfo=UTC)
        assert reporting_date(instant) == date(2026, 7, 22)
    finally:
        clear_reporting_timezone_cache()


def test_overdue_helpers_use_organization_date(monkeypatch):
    today = date(2026, 7, 22)
    monkeypatch.setattr(financial, "reporting_date", lambda: today)

    assert financial.check_is_overdue("sent", date(2026, 7, 21))
    assert not financial.check_is_overdue("sent", today)
    assert financial.calculate_days_overdue("sent", date(2026, 7, 20)) == 2


def test_invoice_overdue_values_use_organization_date(monkeypatch):
    today = date(2026, 7, 22)
    monkeypatch.setattr(financial, "reporting_date", lambda: today)

    invoice = Invoice(
        status=InvoiceStatus.SENT,
        due_date=date(2026, 7, 21),
        balance_due=Decimal("1.00"),
    )
    assert invoice.is_overdue is True
    assert invoice.days_overdue == 1

    summary = InvoiceSummary(
        id=uuid4(),
        invoice_number="INV-LOCAL-DATE",
        invoice_reference="IN-LOCAL-DATE",
        customer_id=uuid4(),
        customer_name="Local Date Customer",
        transaction_date=date(2026, 7, 1),
        due_date=date(2026, 7, 21),
        status=InvoiceStatus.SENT,
        currency=Currency.KES,
        total_due=Decimal("1.00"),
        balance_due=Decimal("1.00"),
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert summary.is_overdue is True
    assert summary.days_overdue == 1


def test_quote_expiry_values_use_organization_date(monkeypatch):
    today = date(2026, 7, 22)
    monkeypatch.setattr(financial, "reporting_date", lambda: today)
    monkeypatch.setattr(quote_models, "reporting_date", lambda: today)
    monkeypatch.setattr(quote_schemas, "reporting_date", lambda: today)

    quote = Quote(status=QuoteStatus.SENT, due_date=date(2026, 7, 21))
    assert quote.is_expired is True
    assert quote.days_until_expiry == -1

    summary = QuoteSummary(
        id=uuid4(),
        quote_number="QUO-LOCAL-DATE",
        quote_reference="QT-LOCAL-DATE",
        customer_id=uuid4(),
        customer_name="Local Date Customer",
        transaction_date=date(2026, 7, 1),
        due_date=date(2026, 7, 21),
        status=QuoteStatus.SENT,
        currency=Currency.KES,
        total_due=Decimal("1.00"),
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert summary.is_expired is True
    assert summary.days_until_expiry == -1
