"""Schemas for the financial statements read model.

Backs the Income Statements and Cashflow pages. See
``app.modules.statements.queries`` for the accounting basis (what counts
as revenue / expense and how amounts are measured).

Period semantics
----------------
All period presets resolve against *today in UTC* to a closed
``[date_from, date_to]`` window. Every window also carries the
immediately preceding window of equal length, used for the overview
deltas, so comparisons are deterministic for any window shape.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.common.exceptions import BadRequestException
from app.constants.enums import Currency

#: Upper bound for a custom reporting window. Bounds the cost of the
#: aggregate queries and rejects obviously absurd inputs (e.g. year 0001).
MAX_CUSTOM_RANGE_DAYS = 1830  # ~5 years


class RangePreset(StrEnum):
    """Reporting period presets offered by the statements UI."""

    LAST_7_DAYS = "last_7_days"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_QUARTER = "this_quarter"
    THIS_YEAR = "this_year"
    CUSTOM = "custom"


class ResolvedPeriod(BaseModel):
    """A closed reporting window plus its equal-length predecessor.

    ``previous_*`` is the window of identical length ending the day
    before ``date_from`` — the baseline for all change percentages.
    """

    range: RangePreset
    date_from: date
    date_to: date
    previous_date_from: date
    previous_date_to: date

    @classmethod
    def resolve(
        cls,
        preset: RangePreset,
        date_from: date | None = None,
        date_to: date | None = None,
        today: date | None = None,
    ) -> "ResolvedPeriod":
        """Resolve a preset (or custom dates) into concrete UTC windows.

        Raises BadRequestException (400) for invalid custom ranges or for
        dates supplied alongside a non-custom preset, so behaviour is
        explicit rather than silently ignoring input.
        """
        today = today or datetime.now(UTC).date()

        if preset is RangePreset.CUSTOM:
            if date_from is None or date_to is None:
                raise BadRequestException(
                    "range=custom requires both dateFrom and dateTo",
                    field="dateFrom",
                )
            if date_from > date_to:
                raise BadRequestException(
                    "dateFrom must be on or before dateTo", field="dateFrom"
                )
            length_days = (date_to - date_from).days + 1
            if length_days > MAX_CUSTOM_RANGE_DAYS:
                raise BadRequestException(
                    f"Custom range may not exceed {MAX_CUSTOM_RANGE_DAYS} days",
                    field="dateTo",
                )
            start, end = date_from, date_to
        else:
            if date_from is not None or date_to is not None:
                raise BadRequestException(
                    "dateFrom/dateTo are only supported with range=custom",
                    field="range",
                )
            if preset is RangePreset.LAST_7_DAYS:
                start, end = today - timedelta(days=6), today
            elif preset is RangePreset.THIS_MONTH:
                start, end = today.replace(day=1), today
            elif preset is RangePreset.LAST_MONTH:
                end = today.replace(day=1) - timedelta(days=1)
                start = end.replace(day=1)
            elif preset is RangePreset.THIS_QUARTER:
                quarter_month = 3 * ((today.month - 1) // 3) + 1
                start, end = date(today.year, quarter_month, 1), today
            else:  # RangePreset.THIS_YEAR
                start, end = date(today.year, 1, 1), today

        length = (end - start).days + 1
        prev_to = start - timedelta(days=1)
        prev_from = prev_to - timedelta(days=length - 1)

        return cls(
            range=preset,
            date_from=start,
            date_to=end,
            previous_date_from=prev_from,
            previous_date_to=prev_to,
        )


# Overview


class OverviewMetric(BaseModel):
    """A money metric with its previous-period baseline and delta."""

    amount: Decimal
    previous_amount: Decimal
    change_percent: float | None = Field(
        default=None,
        description=(
            "Percent change vs the previous period. Null when the previous "
            "period is zero (a 0 -> x jump has no meaningful percentage)."
        ),
    )


class ProfitMarginMetric(BaseModel):
    """Profit margin as a percentage of revenue."""

    percent: float | None = Field(
        default=None,
        description="net_profit / revenue * 100. Null when revenue is zero.",
    )
    previous_percent: float | None = None
    change_points: float | None = Field(
        default=None,
        description=(
            "Percentage-point delta vs the previous period. Null when "
            "either margin is undefined."
        ),
    )


class StatementOverviewResponse(BaseModel):
    """Overview cards shared by the income statement and cashflow pages."""

    period: ResolvedPeriod
    currency: Currency
    total_revenue: OverviewMetric
    total_expenses: OverviewMetric
    net_profit: OverviewMetric
    profit_margin: ProfitMarginMetric


# Income statement


class IncomeStatementLine(BaseModel):
    """One account-category row in a statement section."""

    account_category: str
    description: str | None = Field(
        default=None,
        description=(
            "Representative line-item description for the category "
            "(deterministic: lexicographic minimum across the group)."
        ),
    )
    document_count: int
    amount: Decimal


class IncomeStatementSection(BaseModel):
    """A statement section; total always equals the sum of its lines."""

    line_items: list[IncomeStatementLine]
    total: Decimal


class IncomeStatementResponse(BaseModel):
    period: ResolvedPeriod
    currency: Currency
    operating_revenue: IncomeStatementSection
    operating_expenses: IncomeStatementSection
    net_profit: Decimal


# Cashflow


class CashflowEntry(BaseModel):
    """One signed ledger row derived from a recognized source document."""

    id: str = Field(description="Stable ledger key: '<source_type>:<source_id>'")
    source_id: UUID
    source_type: Literal["quote_invoice", "expense"]
    entity_name: str
    ref_no: str
    category: Literal["income", "expense"]
    date: date
    amount: Decimal = Field(
        description="Signed, tax-inclusive total: income positive, expense negative"
    )


class CashflowCounts(BaseModel):
    """Entry counts for the cashflow filter tabs."""

    all: int
    income: int
    expense: int
