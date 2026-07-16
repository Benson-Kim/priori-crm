"""Ledger service: posting rules, trial balance, balance sheet, backfill.

Posting contract:
- called from document services in the SAME transaction as the mutation,
  so a rolled-back document change never leaves a phantom entry and a
  committed one is never missing its posting;
- ``post_entry`` refuses unbalanced input before anything is written and
  dedupes on (source_type, source_id), so every posting is idempotent -
  which is also what makes the backfill safely re-runnable;
- reversals are mirrored entries dated TODAY: a cancellation is an event
  of the current period and must never be backdated into a closed one.

Quotes and purchase orders deliberately post nothing: a quote is an
offer, and a PO is not a liability until billed (vendor bills, #10).
"""

import logging
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.common.exceptions import BadRequestException
from app.modules.ledger.models import Account, JournalEntry, JournalLine

logger = logging.getLogger(__name__)

# Built-in chart of accounts. Accounts are get-or-created on first use so
# the ledger works identically under Alembic-migrated and create_all test
# schemas without a seed step.
CASH = "1000"
ACCOUNTS_RECEIVABLE = "1100"
VAT_INPUT = "1200"
ACCOUNTS_PAYABLE = "2000"
VAT_OUTPUT = "2100"
RETAINED_EARNINGS = "3000"
REVENUE = "4000"
OPERATING_EXPENSES = "5000"

_CHART: dict[str, tuple[str, str]] = {
    CASH: ("Cash and Bank", "asset"),
    ACCOUNTS_RECEIVABLE: ("Accounts Receivable", "asset"),
    VAT_INPUT: ("VAT Input Receivable", "asset"),
    ACCOUNTS_PAYABLE: ("Accounts Payable", "liability"),
    VAT_OUTPUT: ("VAT Output Payable", "liability"),
    RETAINED_EARNINGS: ("Retained Earnings", "equity"),
    REVENUE: ("Sales Revenue", "income"),
    OPERATING_EXPENSES: ("Operating Expenses", "expense"),
}

#: Debit-normal account types; the rest are credit-normal.
_DEBIT_NORMAL = {"asset", "expense"}


class LedgerService:
    """Double-entry posting and ledger-derived reports."""

    def __init__(self, db: Session, current_user=None) -> None:
        self._db = db
        self._current_user = current_user

    # accounts

    def _account(self, code: str) -> Account:
        """Get or create a built-in chart account by code."""
        account = self._db.query(Account).filter(Account.code == code).first()
        if account is not None:
            return account
        name, type_ = _CHART[code]
        account = Account(code=code, name=name, type=type_)
        self._db.add(account)
        self._db.flush()
        return account

    def list_accounts(self) -> list[Account]:
        """All accounts, ordered by code (built-ins created on demand)."""
        for code in _CHART:
            self._account(code)
        return self._db.query(Account).order_by(Account.code).all()

    # posting core

    def _existing(self, source_type: str, source_id: uuid.UUID) -> JournalEntry | None:
        return (
            self._db.query(JournalEntry)
            .filter(
                JournalEntry.source_type == source_type,
                JournalEntry.source_id == source_id,
            )
            .first()
        )

    def post_entry(
        self,
        *,
        entry_date: date,
        source_type: str,
        source_id: uuid.UUID,
        currency: str,
        memo: str,
        lines: list[tuple[str, Decimal, Decimal]],
    ) -> bool:
        """Post one balanced entry; returns False when it already exists.

        ``lines`` are (account_code, debit, credit) tuples; zero-amount
        lines are dropped. Refuses (400) unbalanced or empty input BEFORE
        anything is written - an unbalanced entry must be impossible.
        """
        if self._existing(source_type, source_id) is not None:
            return False

        effective = [
            (code, Decimal(debit or 0), Decimal(credit or 0))
            for code, debit, credit in lines
            if Decimal(debit or 0) != 0 or Decimal(credit or 0) != 0
        ]
        debit_total = sum((line[1] for line in effective), Decimal("0.00"))
        credit_total = sum((line[2] for line in effective), Decimal("0.00"))
        if not effective or debit_total != credit_total or debit_total <= 0:
            raise BadRequestException(
                detail=(
                    f"Journal entry '{source_type}' does not balance "
                    f"(debits {debit_total}, credits {credit_total})."
                ),
                field="lines",
            )

        entry = JournalEntry(
            entry_date=entry_date,
            source_type=source_type,
            source_id=source_id,
            currency=currency,
            memo=memo,
        )
        self._db.add(entry)
        self._db.flush()
        for code, debit, credit in effective:
            self._db.add(
                JournalLine(
                    entry_id=entry.id,
                    account_id=self._account(code).id,
                    debit=debit,
                    credit=credit,
                )
            )
        self._db.flush()
        return True

    def _post_mirror(
        self,
        original: JournalEntry,
        *,
        source_type: str,
        source_id: uuid.UUID,
        memo: str,
    ) -> bool:
        """Post the debit/credit mirror of ``original``, dated today."""
        if self._existing(source_type, source_id) is not None:
            return False
        lines = (
            self._db.query(JournalLine)
            .filter(JournalLine.entry_id == original.id)
            .all()
        )
        entry = JournalEntry(
            entry_date=date.today(),
            source_type=source_type,
            source_id=source_id,
            currency=original.currency,
            memo=memo,
        )
        self._db.add(entry)
        self._db.flush()
        for line in lines:
            self._db.add(
                JournalLine(
                    entry_id=entry.id,
                    account_id=line.account_id,
                    debit=line.credit,
                    credit=line.debit,
                )
            )
        self._db.flush()
        return True

    # posting rules

    def post_invoice_issued(self, invoice) -> bool:
        """DR Accounts Receivable / CR Revenue (+ CR VAT output)."""
        total = Decimal(invoice.total_due or 0)
        if total == 0:
            return False
        tax = Decimal(invoice.tax_total or 0)
        return self.post_entry(
            entry_date=invoice.transaction_date,
            source_type="invoice_issued",
            source_id=invoice.id,
            currency=invoice.currency,
            memo=f"Invoice {invoice.invoice_number} issued",
            lines=[
                (ACCOUNTS_RECEIVABLE, total, Decimal("0.00")),
                (REVENUE, Decimal("0.00"), total - tax),
                (VAT_OUTPUT, Decimal("0.00"), tax),
            ],
        )

    def post_invoice_payment(self, payment, invoice) -> bool:
        """DR Cash / CR Accounts Receivable."""
        amount = Decimal(payment.amount or 0)
        if amount == 0:
            return False
        return self.post_entry(
            entry_date=payment.payment_date,
            source_type="invoice_payment",
            source_id=payment.id,
            currency=invoice.currency,
            memo=f"Payment on invoice {invoice.invoice_number}",
            lines=[
                (CASH, amount, Decimal("0.00")),
                (ACCOUNTS_RECEIVABLE, Decimal("0.00"), amount),
            ],
        )

    def post_invoice_canceled(self, invoice) -> bool:
        """Mirror the issue entry (if one exists), dated today."""
        original = self._existing("invoice_issued", invoice.id)
        if original is None:
            return False  # canceled from DRAFT: nothing was ever posted
        return self._post_mirror(
            original,
            source_type="invoice_canceled",
            source_id=invoice.id,
            memo=f"Invoice {invoice.invoice_number} canceled",
        )

    def post_expense_recorded(self, expense) -> bool:
        """DR Operating Expenses (+ DR VAT input) / CR Accounts Payable."""
        total = Decimal(expense.total_due or 0)
        if total == 0:
            return False
        tax = Decimal(expense.tax_total or 0)
        return self.post_entry(
            entry_date=expense.expense_date,
            source_type="expense_recorded",
            source_id=expense.id,
            currency=expense.currency,
            memo=f"Expense {expense.expense_number} recorded",
            lines=[
                (OPERATING_EXPENSES, total - tax, Decimal("0.00")),
                (VAT_INPUT, tax, Decimal("0.00")),
                (ACCOUNTS_PAYABLE, Decimal("0.00"), total),
            ],
        )

    def post_expense_payment(self, payment, expense) -> bool:
        """DR Accounts Payable / CR Cash."""
        amount = Decimal(payment.amount or 0)
        if amount == 0:
            return False
        return self.post_entry(
            entry_date=payment.payment_date,
            source_type="expense_payment",
            source_id=payment.id,
            currency=expense.currency,
            memo=f"Payment on expense {expense.expense_number}",
            lines=[
                (ACCOUNTS_PAYABLE, amount, Decimal("0.00")),
                (CASH, Decimal("0.00"), amount),
            ],
        )

    def post_expense_canceled(self, expense) -> bool:
        """Mirror the recorded entry (if one exists), dated today."""
        original = self._existing("expense_recorded", expense.id)
        if original is None:
            return False
        return self._post_mirror(
            original,
            source_type="expense_canceled",
            source_id=expense.id,
            memo=f"Expense {expense.expense_number} canceled",
        )

    # reports

    def trial_balance(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        """Per account and currency: debit/credit totals + normal balance."""
        query = (
            self._db.query(
                Account.code,
                Account.name,
                Account.type,
                JournalEntry.currency,
                func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
                func.coalesce(func.sum(JournalLine.credit), 0).label("credit"),
            )
            .join(JournalLine, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        )
        if date_from is not None:
            query = query.filter(JournalEntry.entry_date >= date_from)
        if date_to is not None:
            query = query.filter(JournalEntry.entry_date <= date_to)
        rows = (
            query.group_by(
                Account.code, Account.name, Account.type, JournalEntry.currency
            )
            .order_by(Account.code, JournalEntry.currency)
            .all()
        )
        result = []
        for code, name, type_, currency, debit, credit in rows:
            debit = Decimal(str(debit))
            credit = Decimal(str(credit))
            balance = (
                debit - credit if type_ in _DEBIT_NORMAL else credit - debit
            )
            result.append(
                {
                    "account_code": code,
                    "account_name": name,
                    "account_type": type_,
                    "currency": currency,
                    "debit_total": debit,
                    "credit_total": credit,
                    "balance": balance,
                }
            )
        return result

    def balance_sheet(self, as_of: date | None = None) -> list[dict]:
        """Assets vs liabilities + equity per currency, as of a date.

        Until closing entries exist (#8 close procedure), the period's net
        income is derived on the fly and shown as a synthetic 'Current
        earnings' equity line, so the statement always balances.
        """
        rows = self.trial_balance(date_to=as_of or date.today())
        by_currency: dict[str, list[dict]] = {}
        for row in rows:
            by_currency.setdefault(row["currency"], []).append(row)

        sheets = []
        for currency, currency_rows in sorted(by_currency.items()):
            sections: dict[str, list[dict]] = {
                "asset": [],
                "liability": [],
                "equity": [],
            }
            net_income = Decimal("0.00")
            for row in currency_rows:
                item = {
                    "account_code": row["account_code"],
                    "account_name": row["account_name"],
                    "balance": row["balance"],
                }
                if row["account_type"] in sections:
                    sections[row["account_type"]].append(item)
                elif row["account_type"] == "income":
                    net_income += row["balance"]
                else:  # expense
                    net_income -= row["balance"]

            sections["equity"].append(
                {
                    "account_code": "3999",
                    "account_name": "Current earnings",
                    "balance": net_income,
                }
            )
            total_assets = sum(
                (i["balance"] for i in sections["asset"]), Decimal("0.00")
            )
            total_liab_equity = sum(
                (
                    i["balance"]
                    for i in sections["liability"] + sections["equity"]
                ),
                Decimal("0.00"),
            )
            sheets.append(
                {
                    "currency": currency,
                    "assets": sections["asset"],
                    "liabilities": sections["liability"],
                    "equity": sections["equity"],
                    "total_assets": total_assets,
                    "total_liabilities_and_equity": total_liab_equity,
                    "balanced": total_assets == total_liab_equity,
                }
            )
        return sheets

    # backfill

    def backfill(self) -> dict[str, int]:
        """Idempotently post history from existing documents and payments.

        Safe to re-run: every posting dedupes on (source_type, source_id).
        One-off operator tool (see the internal endpoint); per-row queries
        are acceptable here and keep the logic identical to live posting.
        """
        from app.modules.expenses.models import Expense, ExpensePayment
        from app.modules.invoices.models import Invoice, Payment

        counts = {
            "invoices_issued": 0,
            "invoice_reversals": 0,
            "invoice_payments": 0,
            "expenses_recorded": 0,
            "expense_reversals": 0,
            "expense_payments": 0,
        }

        issued = (
            self._db.query(Invoice).filter(Invoice.sent_at.isnot(None)).all()
        )
        for invoice in issued:
            if self.post_invoice_issued(invoice):
                counts["invoices_issued"] += 1
            if str(invoice.status) == "canceled" and self.post_invoice_canceled(
                invoice
            ):
                counts["invoice_reversals"] += 1

        for payment in self._db.query(Payment).all():
            invoice = (
                self._db.query(Invoice)
                .filter(Invoice.id == payment.invoice_id)
                .first()
            )
            if invoice is not None and self.post_invoice_payment(payment, invoice):
                counts["invoice_payments"] += 1

        for expense in self._db.query(Expense).all():
            if self.post_expense_recorded(expense):
                counts["expenses_recorded"] += 1
            if str(expense.status) == "canceled" and self.post_expense_canceled(
                expense
            ):
                counts["expense_reversals"] += 1

        for payment in self._db.query(ExpensePayment).all():
            expense = (
                self._db.query(Expense)
                .filter(Expense.id == payment.expense_id)
                .first()
            )
            if expense is not None and self.post_expense_payment(payment, expense):
                counts["expense_payments"] += 1

        logger.info("Ledger backfill complete", extra={"counts": counts})
        return counts
