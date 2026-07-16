"""
Deep module for generating statements of account (customer/vendor).

Consolidates the chronological merging of invoices/expenses and payments,
running balance computations, and ledger presentation into a single
generic algorithm.  Both CustomerService and VendorService delegate to
this module rather than reimplementing the same loop.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.common.financial import quantize_money

# Default statement window when the caller does not supply explicit bounds.
DEFAULT_STATEMENT_PERIOD_DAYS = 365

#: Statuses excluded from statement balances: drafts are not yet
#: receivable/payable, and canceled documents — including payments recorded
#: against them — must not move the balance. Plain strings so the predicate
#: applies to invoices (draft/canceled) and expenses (canceled) alike.
#:
#: This is the single source of truth for exclusions applied symmetrically to
#: the debit side (documents) and credit side (payments recorded against them)
#: in both customer and vendor statement services. Asymmetric or per-service
#: copies of this rule are how opening balances can drift after cancellations.
EXCLUDED_STATEMENT_STATUSES: tuple[str, ...] = ("draft", "canceled")


def default_statement_period(
    period_start: date | None = None,
    period_end: date | None = None,
) -> tuple[date, date]:
    """Resolve a statement period, defaulting to the last 12 months.

    Shared by the customer and vendor statement endpoints so both expose the
    same defaulting behaviour: ``period_end`` defaults to today and
    ``period_start`` defaults to ``period_end`` minus 12 months.
    """
    if period_end is None:
        period_end = date.today()
    if period_start is None:
        period_start = period_end - timedelta(days=DEFAULT_STATEMENT_PERIOD_DAYS)
    return period_start, period_end


@dataclass(frozen=True, slots=True)
class DebitEntry:
    """A charge row (invoice / expense) to be added to the ledger."""

    date: date
    description: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CreditEntry:
    """A payment row to be subtracted from the ledger."""

    date: date
    description: str
    amount: Decimal


class StatementGenerator:
    """
    Pure-function generator that merges debits and credits into a
    chronological ledger with a running balance.

    Returns plain dicts so that callers can wrap them in their own
    domain-specific Pydantic schemas (StatementTransaction,
    VendorStatementTransaction, etc.) without coupling.
    """

    @staticmethod
    def generate(
        *,
        opening_balance: Decimal,
        period_start: date,
        debits: list[DebitEntry],
        credits: list[CreditEntry],
    ) -> tuple[list[dict], dict]:
        """
        Build a statement ledger.

        Args:
            opening_balance: Balance carried forward from before period_start.
            period_start: First day of the statement period (used in opening row).
            debits: Charge entries (invoices, expenses) within the period.
            credits: Payment entries within the period.

        Returns:
            (transactions, summary) where:
                transactions — list of dicts with keys:
                    date, description, amount, payment, balance
                summary — dict with keys:
                    opening_balance, invoiced_amount, amount_paid, balance_due
        """
        transactions: list[dict] = []
        running_balance = opening_balance

        # Opening balance row
        transactions.append(
            {
                "date": period_start,
                "description": "Opening Balance",
                "amount": opening_balance,
                "payment": Decimal("0.00"),
                "balance": opening_balance,
            }
        )

        # Tag and merge — debits sort_key=0 so they appear before credits
        # on the same date (invoice first, then payment).
        tagged: list[tuple[date, int, str, DebitEntry | CreditEntry]] = []
        for d in debits:
            tagged.append((d.date, 0, "debit", d))
        for c in credits:
            tagged.append((c.date, 1, "credit", c))

        tagged.sort(key=lambda t: (t[0], t[1]))

        invoiced_amount = Decimal("0.00")
        amount_paid = Decimal("0.00")

        for _, _, kind, entry in tagged:
            if kind == "debit":
                running_balance = quantize_money(running_balance + entry.amount)
                invoiced_amount = quantize_money(invoiced_amount + entry.amount)
                transactions.append(
                    {
                        "date": entry.date,
                        "description": entry.description,
                        "amount": entry.amount,
                        "payment": Decimal("0.00"),
                        "balance": running_balance,
                    }
                )
            else:
                running_balance = quantize_money(running_balance - entry.amount)
                amount_paid = quantize_money(amount_paid + entry.amount)
                transactions.append(
                    {
                        "date": entry.date,
                        "description": entry.description,
                        "amount": Decimal("0.00"),
                        "payment": entry.amount,
                        "balance": running_balance,
                    }
                )

        summary = {
            "opening_balance": opening_balance,
            "invoiced_amount": invoiced_amount,
            "amount_paid": amount_paid,
            "balance_due": running_balance,
        }

        return transactions, summary
