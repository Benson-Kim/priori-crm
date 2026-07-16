"""General ledger (issue #7): double-entry postings and reports.

Contract pinned here:
- post_entry refuses unbalanced/empty input before writing anything and
  dedupes on (source_type, source_id) - postings are idempotent;
- invoice issue posts DR AR / CR Revenue / CR VAT-output split exactly
  as persisted on the document (document-level VAT included);
- expense create posts DR Expense (+ DR VAT input) / CR AP; the payment
  chokepoint posts DR AP / CR Cash (mark_as_paid covered);
- cancellation posts a today-dated mirror of the original entry;
- the trial balance always balances and the balance sheet's assets
  equal liabilities + equity (current earnings derived);
- backfill is idempotent (second run creates nothing);
- parity: ledger revenue equals document-derived revenue.

Document create paths use advisory-lock reference generation, so those
cases are PostgreSQL-only; pure posting-core cases run everywhere.
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
    TaxType,
    VendorStatus,
)
from app.modules.customers.models import Customer
from app.modules.invoices.schemas import InvoiceCreate
from app.modules.invoices.service import InvoiceService
from app.modules.ledger.models import JournalEntry, JournalLine
from app.modules.ledger.service import (
    ACCOUNTS_PAYABLE,
    ACCOUNTS_RECEIVABLE,
    CASH,
    OPERATING_EXPENSES,
    REVENUE,
    VAT_INPUT,
    VAT_OUTPUT,
    LedgerService,
)
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

pg_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Create uses advisory-lock reference generation (PostgreSQL only)",
)


def _customer(db) -> Customer:
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


def _vendor(db) -> Vendor:
    vendor = Vendor(
        vendor_name="Acme Supplies",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _invoice_payload(customer_id, **kw) -> InvoiceCreate:
    base = {
        "customer_id": str(customer_id),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "line_items": [
            {
                "item_name": "Item",
                "description": "desc",
                "quantity": 2,
                "unit_price": "100.00",
                "tax_type": TaxType.NO_TAX.value,
            }
        ],
    }
    base.update(kw)
    return InvoiceCreate.model_validate(base)


def _lines_by_code(db, ledger, entry) -> dict[str, tuple[Decimal, Decimal]]:
    result: dict[str, tuple[Decimal, Decimal]] = {}
    for line in db.query(JournalLine).filter(JournalLine.entry_id == entry.id):
        account = ledger.list_accounts()
        code = next(a.code for a in account if a.id == line.account_id)
        result[code] = (Decimal(line.debit), Decimal(line.credit))
    return result


# posting core (any backend)


class TestPostEntryCore:
    def test_unbalanced_entry_rejected_before_write(self, db):
        ledger = LedgerService(db)
        with pytest.raises(BadRequestException):
            ledger.post_entry(
                entry_date=date.today(),
                source_type="test_entry",
                source_id=uuid.uuid4(),
                currency="KES",
                memo="unbalanced",
                lines=[
                    (CASH, Decimal("100.00"), Decimal("0.00")),
                    (REVENUE, Decimal("0.00"), Decimal("90.00")),
                ],
            )
        assert db.query(JournalEntry).count() == 0

    def test_empty_entry_rejected(self, db):
        with pytest.raises(BadRequestException):
            LedgerService(db).post_entry(
                entry_date=date.today(),
                source_type="test_entry",
                source_id=uuid.uuid4(),
                currency="KES",
                memo="empty",
                lines=[],
            )

    def test_posting_is_idempotent_per_source(self, db):
        ledger = LedgerService(db)
        source_id = uuid.uuid4()
        lines = [
            (CASH, Decimal("50.00"), Decimal("0.00")),
            (REVENUE, Decimal("0.00"), Decimal("50.00")),
        ]
        first = ledger.post_entry(
            entry_date=date.today(),
            source_type="test_entry",
            source_id=source_id,
            currency="KES",
            memo="once",
            lines=lines,
        )
        second = ledger.post_entry(
            entry_date=date.today(),
            source_type="test_entry",
            source_id=source_id,
            currency="KES",
            memo="twice",
            lines=lines,
        )
        assert first is True
        assert second is False
        assert db.query(JournalEntry).count() == 1

    def test_trial_balance_always_balances(self, db):
        ledger = LedgerService(db)
        ledger.post_entry(
            entry_date=date.today(),
            source_type="test_entry",
            source_id=uuid.uuid4(),
            currency="KES",
            memo="sale",
            lines=[
                (ACCOUNTS_RECEIVABLE, Decimal("116.00"), Decimal("0.00")),
                (REVENUE, Decimal("0.00"), Decimal("100.00")),
                (VAT_OUTPUT, Decimal("0.00"), Decimal("16.00")),
            ],
        )
        rows = ledger.trial_balance()
        debit = sum(r["debit_total"] for r in rows)
        credit = sum(r["credit_total"] for r in rows)
        assert debit == credit == Decimal("116.00")


# document postings (PostgreSQL-backed)


@pg_only
class TestInvoicePostings:
    def test_issue_posts_ar_revenue_vat_split(self, db):
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(
            _invoice_payload(customer.id, vat_enabled=True, vat_rate="0.16")
        )
        assert db.query(JournalEntry).count() == 0  # drafts post nothing

        service.mark_as_sent(invoice.id)

        ledger = LedgerService(db)
        entry = (
            db.query(JournalEntry)
            .filter(
                JournalEntry.source_type == "invoice_issued",
                JournalEntry.source_id == invoice.id,
            )
            .one()
        )
        by_code = _lines_by_code(db, ledger, entry)
        assert by_code[ACCOUNTS_RECEIVABLE][0] == Decimal("232.00")
        assert by_code[REVENUE][1] == Decimal("200.00")
        assert by_code[VAT_OUTPUT][1] == Decimal("32.00")

    def test_cancel_posts_today_dated_mirror(self, db):
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(_invoice_payload(customer.id))
        service.mark_as_sent(invoice.id)
        service.cancel_invoice(invoice.id)

        reversal = (
            db.query(JournalEntry)
            .filter(
                JournalEntry.source_type == "invoice_canceled",
                JournalEntry.source_id == invoice.id,
            )
            .one()
        )
        assert reversal.entry_date == date.today()
        ledger = LedgerService(db)
        by_code = _lines_by_code(db, ledger, reversal)
        # Mirror: AR is credited back, revenue debited back.
        assert by_code[ACCOUNTS_RECEIVABLE][1] == Decimal("200.00")
        assert by_code[REVENUE][0] == Decimal("200.00")

    def test_cancel_from_draft_posts_nothing(self, db):
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(_invoice_payload(customer.id))
        service.cancel_invoice(invoice.id)
        assert db.query(JournalEntry).count() == 0


@pg_only
class TestExpensePostings:
    def _expense(self, db):
        from app.modules.expenses.schemas import ExpenseCreate
        from app.modules.expenses.service import ExpenseService

        vendor = _vendor(db)
        payload = ExpenseCreate.model_validate(
            {
                "vendor_id": str(vendor.id),
                "expense_date": date.today().isoformat(),
                "due_date": (date.today() + timedelta(days=30)).isoformat(),
                "line_items": [
                    {
                        "item_name": "Item",
                        "description": "desc",
                        "quantity": 1,
                        "unit_price": "100.00",
                        "tax_type": TaxType.VAT_16.value,
                    }
                ],
            }
        )
        return ExpenseService(db), ExpenseService(db).create(payload)

    def test_create_posts_expense_vat_ap_split(self, db):
        _, expense = self._expense(db)
        entry = (
            db.query(JournalEntry)
            .filter(
                JournalEntry.source_type == "expense_recorded",
                JournalEntry.source_id == expense.id,
            )
            .one()
        )
        ledger = LedgerService(db)
        by_code = _lines_by_code(db, ledger, entry)
        assert by_code[OPERATING_EXPENSES][0] == expense.subtotal
        assert by_code[VAT_INPUT][0] == expense.tax_total
        assert by_code[ACCOUNTS_PAYABLE][1] == expense.total_due

    def test_mark_as_paid_posts_ap_cash_entry(self, db):
        service, expense = self._expense(db)
        service.mark_as_paid(expense.id)
        entries = (
            db.query(JournalEntry)
            .filter(JournalEntry.source_type == "expense_payment")
            .all()
        )
        assert len(entries) == 1
        ledger = LedgerService(db)
        by_code = _lines_by_code(db, ledger, entries[0])
        assert by_code[ACCOUNTS_PAYABLE][0] == expense.total_due
        assert by_code[CASH][1] == expense.total_due


@pg_only
class TestReportsAndBackfill:
    def test_balance_sheet_balances_after_activity(self, db):
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(
            _invoice_payload(customer.id, vat_enabled=True, vat_rate="0.16")
        )
        service.mark_as_sent(invoice.id)

        sheets = LedgerService(db).balance_sheet()
        assert len(sheets) == 1
        sheet = sheets[0]
        assert sheet["balanced"] is True
        assert sheet["total_assets"] == Decimal("232.00")

    def test_ledger_revenue_matches_document_revenue(self, db):
        customer = _customer(db)
        service = InvoiceService(db)
        for _ in range(2):
            invoice = service.create(
                _invoice_payload(customer.id, vat_enabled=True, vat_rate="0.16")
            )
            service.mark_as_sent(invoice.id)

        rows = LedgerService(db).trial_balance()
        revenue = next(r for r in rows if r["account_code"] == REVENUE)
        # Two invoices, each 200.00 net of VAT: document-derived revenue.
        assert revenue["balance"] == Decimal("400.00")

    def test_backfill_is_idempotent(self, db):
        customer = _customer(db)
        service = InvoiceService(db)
        invoice = service.create(_invoice_payload(customer.id))
        service.mark_as_sent(invoice.id)

        before = db.query(JournalEntry).count()
        counts = LedgerService(db).backfill()
        assert sum(counts.values()) == 0  # live hooks already posted it
        assert db.query(JournalEntry).count() == before
