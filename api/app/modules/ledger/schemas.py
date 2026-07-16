"""Pydantic schemas for ledger reports."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class AccountResponse(BaseModel):
    """Chart-of-accounts entry."""

    id: UUID
    code: str
    name: str
    type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TrialBalanceRow(BaseModel):
    """One account/currency row of the trial balance."""

    account_code: str
    account_name: str
    account_type: str
    currency: str
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal


class BalanceSheetItem(BaseModel):
    """One balance-sheet line."""

    account_code: str
    account_name: str
    balance: Decimal


class BalanceSheetResponse(BaseModel):
    """Balance sheet for one currency."""

    currency: str
    assets: list[BalanceSheetItem]
    liabilities: list[BalanceSheetItem]
    equity: list[BalanceSheetItem]
    total_assets: Decimal
    total_liabilities_and_equity: Decimal
    balanced: bool


class BackfillResponse(BaseModel):
    """Counts of entries created by one backfill run."""

    invoices_issued: int
    invoice_reversals: int
    invoice_payments: int
    expenses_recorded: int
    expense_reversals: int
    expense_payments: int
