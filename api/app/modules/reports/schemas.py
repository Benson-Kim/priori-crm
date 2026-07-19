"""Pydantic response schemas for the reports module.

Reuses ResolvedPeriod and RangePreset from statements.schemas — the single
source of truth for period semantics and validation.

Decimal amounts are serialized as strings by Pydantic's default JSON encoder
for Decimal. The frontend uses Number(row.amount) to convert.

Aged AR/AP schemas are point-in-time (no period range): as_of_date marks
the snapshot date (today UTC). AgingBuckets carries the five standard buckets
used by all accounting systems (QuickBooks, Xero, Sage).
"""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.common.pagination import PaginationMetadata
from app.constants.enums import Currency
from app.modules.statements.schemas import ResolvedPeriod

# Shared base


class ReportBase(BaseModel):
    """Shared period and currency context in every report response."""

    period: ResolvedPeriod
    currency: Currency


# Sales Report Schemas


class SalesSummaryMetrics(BaseModel):
    """Top-line sales metrics for summary MetricCards."""

    subtotal: Decimal = Field(
        description="Pre-tax gross line totals before document-level discounts"
    )
    discount_total: Decimal = Field(
        description="Total document-level discounts applied (subtotal - net_revenue)"
    )
    net_revenue: Decimal = Field(
        description="Net revenue after discount, pre-tax (SUM of total_due - tax_total)"
    )
    tax_collected: Decimal = Field(description="Sum of Invoice.tax_total")
    total_invoiced: Decimal = Field(
        description="Sum of Invoice.total_due (tax-inclusive)"
    )
    invoice_count: int
    outstanding_balance: Decimal = Field(
        description="Sum of balance_due for SENT/PARTIAL/OVERDUE invoices"
    )
    overdue_balance: Decimal = Field(
        description="Sum of balance_due for OVERDUE invoices only"
    )


class RevenueByCustomer(BaseModel):
    customer_id: UUID = Field(
        description="Stable customer ID for row keys (avoids merging duplicate names)",
    )
    customer_name: str
    invoice_count: int
    amount: Decimal


class RevenueByCategory(BaseModel):
    category: str
    document_count: int
    amount: Decimal


class SalesReportSummaryResponse(ReportBase):
    """Sales report overview: metrics + breakdowns."""

    metrics: SalesSummaryMetrics
    revenue_by_customer: list[RevenueByCustomer]
    revenue_by_category: list[RevenueByCategory]


class SalesLedgerEntry(BaseModel):
    """One invoice row in the sales ledger."""

    id: UUID
    customer_name: str
    reference: str
    number: str
    date: date
    status: str
    currency: str
    subtotal: Decimal = Field(
        description="Invoice.subtotal — pre-tax gross of line items"
    )
    discount: Decimal = Field(description="Document-level discount applied")
    net_revenue: Decimal = Field(description="subtotal - discount (pre-tax net)")
    amount: Decimal = Field(description="Invoice.total_due (tax-inclusive)")
    tax: Decimal = Field(description="Invoice.tax_total")
    balance_due: Decimal


class SalesStatusCounts(BaseModel):
    """Per-status counts for the sales filter tabs (recognized statuses only)."""

    all: int
    sent: int = 0
    partial: int = 0
    paid: int = 0
    overdue: int = 0


# Purchases Report Schemas


class PurchasesSummaryMetrics(BaseModel):
    """Top-line purchases metrics for summary MetricCards."""

    expense_spend: Decimal = Field(
        description="Pre-tax expense spend (SUM ExpenseLineItem.line_total)"
    )
    po_spend: Decimal = Field(
        description="Pre-tax PO spend (SUM PurchaseOrder.subtotal)"
    )
    total_spend: Decimal = Field(description="expense_spend + po_spend")
    expense_tax: Decimal
    po_tax: Decimal
    total_tax: Decimal = Field(description="expense_tax + po_tax")
    expense_count: int
    po_count: int
    outstanding_balance: Decimal = Field(
        description="Combined unpaid balance (expenses + sent POs)"
    )


class SpendByVendor(BaseModel):
    vendor_id: UUID
    vendor_name: str
    amount: Decimal


class PurchasesReportSummaryResponse(ReportBase):
    """Purchases report overview: metrics + top vendor breakdown."""

    metrics: PurchasesSummaryMetrics
    spend_by_vendor: list[SpendByVendor]


class PurchasesLedgerEntry(BaseModel):
    """One row in the combined expenses + POs ledger."""

    source_id: UUID
    source_type: Literal["expense", "purchase_order"]
    entity_name: str
    reference: str
    number: str
    category: Literal["expense", "purchase_order"]
    entry_date: date
    status: str
    currency: str
    amount: Decimal = Field(
        description="Tax-inclusive total (Expense.total_due or PurchaseOrder.total)"
    )
    tax: Decimal
    balance_due: Decimal


class PurchasesSourceCounts(BaseModel):
    """Per-source counts for the purchases filter tabs."""

    all: int
    expense: int
    purchase_order: int


# Tax Report Schemas


class TaxSummaryMetrics(BaseModel):
    """VAT position summary."""

    vat_collected: Decimal = Field(
        description="Sales VAT collected (SUM Invoice.tax_total)"
    )
    vat_paid: Decimal = Field(description="Purchase VAT input tax (expenses + POs)")
    net_vat: Decimal = Field(
        description="vat_collected - vat_paid. Positive = payable to KRA."
    )


class TaxByTypeRow(BaseModel):
    """Tax amount for one explicit type/rate combination."""

    tax_type: str
    tax_rate: Decimal | None = Field(
        default=None,
        description="Rate as a fraction; null for exempt/no-tax classifications",
    )
    tax_amount: Decimal
    document_count: int = Field(
        default=0,
        description="Distinct source-document count for this type/rate combination",
    )


class TaxReportResponse(ReportBase):
    """Tax report: VAT position + per-type breakdowns for sales and purchases."""

    metrics: TaxSummaryMetrics
    sales_by_tax_type: list[TaxByTypeRow]
    purchases_by_tax_type: list[TaxByTypeRow]


# Aged Receivables Schemas


class AgingBuckets(BaseModel):
    """Balance split across the five standard aging buckets.

    Industry standard: Current (not yet due), 1-30, 31-60, 61-90, 90+ days.
    All major accounting systems (QuickBooks, Xero, Sage) use this structure.
    """

    current: Decimal = Decimal("0.00")
    days_1_30: Decimal = Decimal("0.00")
    days_31_60: Decimal = Decimal("0.00")
    days_61_90: Decimal = Decimal("0.00")
    days_90_plus: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")

    @classmethod
    def from_bucket_dict(cls, d: dict) -> "AgingBuckets":
        """Construct from the {bucket_key: amount} dict returned by queries."""
        current = d.get("current", Decimal("0"))
        b1_30 = d.get("1_30", Decimal("0"))
        b31_60 = d.get("31_60", Decimal("0"))
        b61_90 = d.get("61_90", Decimal("0"))
        b90p = d.get("90_plus", Decimal("0"))
        return cls(
            current=current,
            days_1_30=b1_30,
            days_31_60=b31_60,
            days_61_90=b61_90,
            days_90_plus=b90p,
            total=current + b1_30 + b31_60 + b61_90 + b90p,
        )


class AgedReceivableRow(BaseModel):
    """One customer row in the aged AR summary grid."""

    customer_id: UUID
    customer_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    days_90_plus: Decimal
    total: Decimal


class AgedReceivablesSummaryResponse(BaseModel):
    """Aged receivables: totals header + per-customer breakdown.

    Point-in-time snapshot: as_of_date is today UTC.
    No date range filter — aging is always computed as of today.
    """

    currency: str
    as_of_date: date
    totals: AgingBuckets
    customers: list[AgedReceivableRow]


class AgedReceivableDetailRow(BaseModel):
    """One invoice row in the aged AR detail table."""

    id: UUID
    customer_name: str
    reference: str
    number: str
    transaction_date: date
    due_date: date
    status: str
    total_due: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    bucket: str = Field(
        description="Aging bucket: current | 1_30 | 31_60 | 61_90 | 90_plus"
    )
    days_overdue: int = Field(
        description="Negative = not yet due; 0 = due today; positive = days overdue"
    )


class AgedReceivablesDetailResponse(BaseModel):
    """Paginated aged AR invoice rows."""

    currency: str
    as_of_date: date
    items: list[AgedReceivableDetailRow]
    metadata: PaginationMetadata


# Aged Payables Schemas


class AgedPayableRow(BaseModel):
    """One vendor row in the aged AP summary grid."""

    vendor_id: UUID
    vendor_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    days_90_plus: Decimal
    total: Decimal


class AgedPayablesSummaryResponse(BaseModel):
    """Aged payables: totals header + per-vendor breakdown.

    Expenses: bucketed by due_date.
    POs (SENT + unpaid): always in 'current' bucket — PurchaseOrder has no due_date.
    """

    currency: str
    as_of_date: date
    totals: AgingBuckets
    vendors: list[AgedPayableRow]


class AgedPayableDetailRow(BaseModel):
    """One expense or PO row in the aged AP detail table."""

    source_id: UUID
    source_type: Literal["expense", "purchase_order"]
    vendor_name: str
    reference: str
    number: str
    entry_date: date
    due_date: date = Field(
        description="For POs: same as order_date (display anchor only, not a payment due date)"
    )
    status: str
    total_due: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    bucket: str
    days_overdue: int


class AgedPayablesDetailResponse(BaseModel):
    """Paginated aged AP rows (expenses + POs combined)."""

    currency: str
    as_of_date: date
    items: list[AgedPayableDetailRow]
    metadata: PaginationMetadata
