"""General-ledger endpoints: chart, trial balance, balance sheet, backfill."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.common.dependencies import LedgerServiceDep, verify_internal_secret
from app.modules.ledger.schemas import (
    AccountResponse,
    BackfillResponse,
    BalanceSheetResponse,
    TrialBalanceRow,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/accounts",
    summary="List the chart of accounts",
    description="All ledger accounts, ordered by code.",
)
def list_accounts(service: LedgerServiceDep) -> list[AccountResponse]:
    return [AccountResponse.model_validate(a) for a in service.list_accounts()]


@router.get(
    "/trial-balance",
    summary="Trial balance",
    description=(
        "Per account and currency: debit/credit totals and the normal-side "
        "balance, optionally restricted to an entry-date range. Total "
        "debits always equal total credits per currency."
    ),
)
def trial_balance(
    service: LedgerServiceDep,
    date_from: Annotated[date | None, Query(description="Entry date from")] = None,
    date_to: Annotated[date | None, Query(description="Entry date to")] = None,
) -> list[TrialBalanceRow]:
    return [
        TrialBalanceRow.model_validate(row)
        for row in service.trial_balance(date_from=date_from, date_to=date_to)
    ]


@router.get(
    "/balance-sheet",
    summary="Balance sheet",
    description=(
        "Assets vs liabilities + equity per currency as of a date. Current "
        "earnings are derived on the fly until closing entries exist."
    ),
)
def balance_sheet(
    service: LedgerServiceDep,
    as_of: Annotated[date | None, Query(description="As-of date")] = None,
) -> list[BalanceSheetResponse]:
    return [
        BalanceSheetResponse.model_validate(sheet)
        for sheet in service.balance_sheet(as_of=as_of)
    ]


@router.post(
    "/internal/backfill",
    include_in_schema=False,
    summary="Backfill the ledger from existing documents (internal)",
    description=(
        "Idempotently post journal entries for all existing invoices, "
        "payments and expenses. Safe to re-run; requires X-Internal-Secret."
    ),
    dependencies=[Depends(verify_internal_secret)],
)
def backfill_ledger(db: Annotated[Session, Depends(get_db)]) -> BackfillResponse:
    from app.modules.ledger.service import LedgerService

    return BackfillResponse.model_validate(LedgerService(db).backfill())
