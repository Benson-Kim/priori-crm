"""Schemas for the dashboard read model.

The dashboard is a thin composition over the statements read model: it
reuses the same ``ResolvedPeriod`` / ``RangePreset`` period semantics and
the same ``OverviewMetric`` delta shape, so the dashboard cards can never
disagree with the statements pages over the same window.

Series semantics
----------------
The cashflow chart splits the closed reporting window into contiguous,
non-overlapping buckets. Bucket size is auto-selected from the window
length (day / week / month) unless the caller pins it explicitly, and
buckets are zero-filled server-side so charts always receive a complete,
stable series.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.constants.enums import Currency
from app.modules.statements.schemas import OverviewMetric, ResolvedPeriod

#: Server-side caps for dashboard widget list sizes.
MAX_TOP_SALES_LIMIT = 20
MAX_TRANSACTIONS_LIMIT = 50

#: Auto-granularity thresholds (inclusive window length in days).
AUTO_DAY_MAX_DAYS = 31
AUTO_WEEK_MAX_DAYS = 182


class Granularity(StrEnum):
    """Bucket size for the dashboard cashflow chart."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


def resolve_granularity(period: ResolvedPeriod) -> Granularity:
    """Pick the bucket size that keeps the chart series readable.

    Deterministic: <= 31 days -> day, <= 182 days -> week, else month.
    Combined with the statements module's ``MAX_CUSTOM_RANGE_DAYS`` cap
    this bounds the series to a small, UI-friendly number of buckets for
    any allowed window.
    """
    length_days = (period.date_to - period.date_from).days + 1
    if length_days <= AUTO_DAY_MAX_DAYS:
        return Granularity.DAY
    if length_days <= AUTO_WEEK_MAX_DAYS:
        return Granularity.WEEK
    return Granularity.MONTH


def build_buckets(
    date_from: date, date_to: date, granularity: Granularity
) -> list[tuple[date, date]]:
    """Split a closed window into contiguous closed bucket windows.

    Buckets are non-overlapping, cover the window exactly, and are
    deterministic: week buckets are 7-day chunks anchored on
    ``date_from``; month buckets follow calendar months, with the first
    and last bucket clipped to the window.
    """
    buckets: list[tuple[date, date]] = []
    start = date_from
    while start <= date_to:
        if granularity is Granularity.DAY:
            end = start
        elif granularity is Granularity.WEEK:
            end = min(start + timedelta(days=6), date_to)
        else:  # Granularity.MONTH
            if start.month == 12:
                month_end = date(start.year, 12, 31)
            else:
                month_end = date(start.year, start.month + 1, 1) - timedelta(days=1)
            end = min(month_end, date_to)
        buckets.append((start, end))
        start = end + timedelta(days=1)
    return buckets


# Summary cards


class CountMetric(BaseModel):
    """A count metric with its previous-period baseline and delta."""

    count: int
    previous_count: int
    change_percent: float | None = Field(
        default=None,
        description=(
            "Percent change vs the previous period. Null when the previous "
            "period count is zero (a 0 -> x jump has no meaningful "
            "percentage)."
        ),
    )


class DashboardSummaryResponse(BaseModel):
    """Metric cards for the dashboard header row.

    ``income`` and ``expenses`` run the exact same pre-tax aggregates as
    the statements overview, so the dashboard and the statements pages
    always agree for the same window.
    """

    period: ResolvedPeriod
    currency: Currency
    balance: OverviewMetric = Field(
        description=(
            "Outstanding receivables: SUM(balance_due) over recognized "
            "invoices issued in the window."
        )
    )
    income: OverviewMetric = Field(
        description=(
            "Pre-tax recognized revenue (same basis as the statements overview)."
        )
    )
    expenses: OverviewMetric = Field(
        description=(
            "Pre-tax recognized expenses (same basis as the statements overview)."
        )
    )
    new_customers: CountMetric = Field(
        description=(
            "Customers created in the window (UTC), excluding soft-deleted "
            "customers. Currency-agnostic by design."
        )
    )


# Cashflow chart series


class CashflowBucket(BaseModel):
    """One zero-filled chart bucket (closed date sub-window)."""

    bucket_start: date
    bucket_end: date
    income: Decimal = Field(description="Tax-inclusive money in (>= 0)")
    expense: Decimal = Field(
        description="Tax-inclusive money out as a positive magnitude (>= 0)"
    )
    net: Decimal = Field(description="income - expense (signed)")


class CashflowSeriesResponse(BaseModel):
    """Complete, zero-filled bucket series for the cashflow chart.

    Measured at tax-inclusive ``total_due`` — the same cash view (and the
    same recognition predicates) as the statements cashflow ledger.
    """

    period: ResolvedPeriod
    currency: Currency
    granularity: Granularity
    total_income: Decimal
    total_expense: Decimal
    net_total: Decimal
    buckets: list[CashflowBucket]


# Top sales


class TopSaleLine(BaseModel):
    """One top-selling item (invoice line items grouped by name)."""

    item_name: str
    units_sold: Decimal = Field(description="SUM(quantity) across recognized invoices")
    document_count: int = Field(
        description="Distinct recognized invoices containing the item"
    )
    amount: Decimal = Field(
        description=(
            "Pre-tax SUM(line_total) — the same measurement as the income "
            "statement's revenue lines."
        )
    )


class TopSalesResponse(BaseModel):
    period: ResolvedPeriod
    currency: Currency
    items: list[TopSaleLine]


# Recent transactions


class DashboardTransaction(BaseModel):
    """One recent recognized document in the signed ledger convention."""

    id: str = Field(description="Stable ledger key: '<source_type>:<source_id>'")
    source_id: UUID
    source_type: Literal["quote_invoice", "expense"]
    ref_no: str
    entity_name: str
    website: str | None = Field(
        default=None, description="Counterparty website, when on file"
    )
    item_name: str | None = Field(
        default=None,
        description="First line item of the source document (by line number)",
    )
    category: Literal["income", "expense"]
    date: date
    recorded_at: datetime = Field(description="When the document was recorded (UTC)")
    amount: Decimal = Field(
        description=("Signed, tax-inclusive total: income positive, expense negative")
    )


class DashboardTransactionsResponse(BaseModel):
    period: ResolvedPeriod
    currency: Currency
    items: list[DashboardTransaction]
