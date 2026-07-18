"""Reports service — orchestration layer over the read-side SQL.

All heavy lifting (predicates, aggregation) lives in
``app.modules.reports.queries``; this layer pivots query results into
typed Pydantic response models.

Read-only: never flushes or commits.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.common.pagination import PaginatedResponse, PaginationMetadata, PaginationParams
from app.constants.enums import Currency
from app.modules.reports.queries import ReportsRepository
from app.modules.reports.schemas import (
    # Sales
    AgedPayableDetailRow,
    AgedPayableRow,
    AgedPayablesDetailResponse,
    AgedPayablesSummaryResponse,
    AgedReceivableDetailRow,
    AgedReceivableRow,
    AgedReceivablesDetailResponse,
    AgedReceivablesSummaryResponse,
    AgingBuckets,
    PurchasesLedgerEntry,
    PurchasesReportSummaryResponse,
    PurchasesSummaryMetrics,
    PurchasesSourceCounts,
    RevenueByCategory,
    RevenueByCustomer,
    SalesLedgerEntry,
    SalesReportSummaryResponse,
    SalesSummaryMetrics,
    SalesStatusCounts,
    SpendByVendor,
    TaxByTypeRow,
    TaxReportResponse,
    TaxSummaryMetrics,
)
from app.modules.statements.schemas import ResolvedPeriod

logger = logging.getLogger(__name__)

_ZERO = Decimal("0.00")


class ReportsService:
    """Read-only reporting service — never mutates state."""

    def __init__(self, db: Session, current_user=None) -> None:
        self.db = db
        self.current_user = current_user
        self.repo = ReportsRepository(db)


    # Sales: Summary

    def get_sales_summary(
        self, period: ResolvedPeriod, currency: Currency
    ) -> SalesReportSummaryResponse:
        """Overview metrics + top-customer + category breakdowns."""
        summary = self.repo.sales_summary(period.date_from, period.date_to, currency)
        cust_rows = self.repo.revenue_by_customer(period.date_from, period.date_to, currency)
        cat_rows = self.repo.revenue_by_category(period.date_from, period.date_to, currency)

        return SalesReportSummaryResponse(
            period=period,
            currency=currency,
            metrics=SalesSummaryMetrics(
                subtotal=summary.subtotal,
                discount_total=summary.discount_total,
                net_revenue=summary.net_revenue,
                tax_collected=summary.tax_collected,
                total_invoiced=summary.total_invoiced,
                invoice_count=summary.invoice_count,
                outstanding_balance=summary.outstanding_balance,
                overdue_balance=summary.overdue_balance,
            ),
            revenue_by_customer=[
                RevenueByCustomer(
                    customer_name=r.customer_name,
                    invoice_count=r.invoice_count,
                    amount=Decimal(str(r.amount)),
                )
                for r in cust_rows
            ],
            revenue_by_category=[
                RevenueByCategory(
                    category=r.category,
                    document_count=r.document_count,
                    amount=Decimal(str(r.amount)),
                )
                for r in cat_rows
            ],
        )

    # Sales: Ledger

    def list_sales_ledger(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        params: PaginationParams,
        *,
        search: str | None = None,
        status: str | None = None,
    ) -> PaginatedResponse[SalesLedgerEntry]:
        """Paginated invoice ledger with sentinel pattern."""
        total = None
        if params.with_total:
            total = self.repo.sales_ledger_total(
                period.date_from, period.date_to, currency,
                search=search, status=status,
            )
        rows = self.repo.sales_ledger(
            period.date_from, period.date_to, currency,
            search=search, status=status,
            offset=params.offset, limit=params.fetch_limit,
        )
        entries = [
            SalesLedgerEntry(
                id=r.id,
                customer_name=r.customer_name,
                reference=r.reference,
                number=r.number,
                date=r.date,
                status=r.status,
                currency=r.currency,
                subtotal=Decimal(str(r.subtotal)),
                discount=Decimal(str(r.discount)),
                net_revenue=Decimal(str(r.net_revenue)),
                amount=Decimal(str(r.amount)),
                tax=Decimal(str(r.tax)),
                balance_due=Decimal(str(r.balance_due)),
            )
            for r in rows
        ]
        return PaginatedResponse.create_from_window(entries, params, total=total)

    def get_sales_ledger_total(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        *,
        search: str | None = None,
        status: str | None = None,
    ) -> int:
        """Exact count for the sales ledger."""
        return self.repo.sales_ledger_total(
            period.date_from, period.date_to, currency,
            search=search, status=status,
        )

    def get_sales_status_counts(
        self, period: ResolvedPeriod, currency: Currency
    ) -> SalesStatusCounts:
        """Per-status filter-tab counts."""
        raw = self.repo.sales_status_counts(period.date_from, period.date_to, currency)
        total = sum(raw.values())
        return SalesStatusCounts(
            all=total,
            sent=raw.get("sent", 0),
            partial=raw.get("partial", 0),
            paid=raw.get("paid", 0),
            overdue=raw.get("overdue", 0),
        )


    # Purchases: Summary

    def get_purchases_summary(
        self, period: ResolvedPeriod, currency: Currency
    ) -> PurchasesReportSummaryResponse:
        """Overview metrics + top-vendor breakdown."""
        summary = self.repo.purchases_summary(period.date_from, period.date_to, currency)
        vendor_rows = self.repo.spend_by_vendor(period.date_from, period.date_to, currency)

        return PurchasesReportSummaryResponse(
            period=period,
            currency=currency,
            metrics=PurchasesSummaryMetrics(
                expense_spend=summary.expense_spend,
                po_spend=summary.po_spend,
                total_spend=summary.total_spend,
                expense_tax=summary.expense_tax,
                po_tax=summary.po_tax,
                total_tax=summary.total_tax,
                expense_count=summary.expense_count,
                po_count=summary.po_count,
                outstanding_balance=summary.outstanding_balance,
            ),
            spend_by_vendor=[
                SpendByVendor(
                    vendor_name=r.vendor_name,
                    amount=Decimal(str(r.amount)),
                )
                for r in vendor_rows
            ],
        )


    # Purchases: Ledger

    def list_purchases_ledger(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        params: PaginationParams,
        *,
        source: str = "all",
        search: str | None = None,
    ) -> PaginatedResponse[PurchasesLedgerEntry]:
        """Paginated combined expense + PO ledger."""
        total = None
        if params.with_total:
            total = self.repo.purchases_ledger_total(
                period.date_from, period.date_to, currency,
                source=source, search=search,
            )
        rows = self.repo.purchases_ledger(
            period.date_from, period.date_to, currency,
            source=source, search=search,
            offset=params.offset, limit=params.fetch_limit,
        )
        entries = [
            PurchasesLedgerEntry(
                source_id=r.source_id,
                source_type=r.source_type,
                entity_name=r.entity_name,
                reference=r.reference,
                number=r.number,
                category=r.category,
                entry_date=r.entry_date,
                status=r.status,
                currency=r.currency,
                amount=Decimal(str(r.amount)),
                tax=Decimal(str(r.tax)),
                balance_due=Decimal(str(r.balance_due)),
            )
            for r in rows
        ]
        return PaginatedResponse.create_from_window(entries, params, total=total)

    def get_purchases_ledger_total(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        *,
        source: str = "all",
        search: str | None = None,
    ) -> int:
        """Exact count for the purchases ledger."""
        return self.repo.purchases_ledger_total(
            period.date_from, period.date_to, currency,
            source=source, search=search,
        )

    def get_purchases_source_counts(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        *,
        search: str | None = None,
    ) -> PurchasesSourceCounts:
        """Per-source filter-tab counts."""
        raw = self.repo.purchases_source_counts(
            period.date_from, period.date_to, currency, search=search
        )
        return PurchasesSourceCounts(
            all=raw.get("all", 0),
            expense=raw.get("expense", 0),
            purchase_order=raw.get("purchase_order", 0),
        )


    # Tax Report

    def get_tax_report(
        self, period: ResolvedPeriod, currency: Currency
    ) -> TaxReportResponse:
        """VAT position + per-type breakdowns for sales and purchases."""
        tax_s = self.repo.tax_summary(period.date_from, period.date_to, currency)
        sales_rows = self.repo.tax_by_type_sales(period.date_from, period.date_to, currency)
        purch_rows = self.repo.tax_by_type_purchases(period.date_from, period.date_to, currency)

        return TaxReportResponse(
            period=period,
            currency=currency,
            metrics=TaxSummaryMetrics(
                vat_collected=tax_s.vat_collected,
                vat_paid=tax_s.vat_paid,
                net_vat=tax_s.net_vat,
            ),
            sales_by_tax_type=[
                TaxByTypeRow(
                    tax_type=r.tax_type,
                    tax_amount=Decimal(str(r.tax_amount)),
                    document_count=int(r.document_count),
                )
                for r in sales_rows
            ],
            purchases_by_tax_type=[
                TaxByTypeRow(
                    tax_type=r.tax_type,
                    tax_amount=Decimal(str(r.tax_amount)),
                    document_count=0,
                )
                for r in purch_rows
            ],
        )


    # Aged Receivables

    def get_aged_receivables(self, currency: str) -> AgedReceivablesSummaryResponse:
        """Pivot flat query rows into per-customer aging grid."""
        today = datetime.now(UTC).date()
        flat_rows = self.repo.aged_receivables_summary(currency)

        # Pivot: customer_name → {bucket: Decimal}
        pivot: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
        for row in flat_rows:
            pivot[row.customer_name][row.bucket] += Decimal(str(row.amount))

        totals_d: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        customers: list[AgedReceivableRow] = []

        for cname in sorted(pivot):
            buckets = pivot[cname]
            cur   = buckets.get("current", _ZERO)
            b130  = buckets.get("1_30",    _ZERO)
            b3160 = buckets.get("31_60",   _ZERO)
            b6190 = buckets.get("61_90",   _ZERO)
            b90p  = buckets.get("90_plus", _ZERO)
            row_total = cur + b130 + b3160 + b6190 + b90p
            customers.append(AgedReceivableRow(
                customer_name=cname,
                current=cur,
                days_1_30=b130,
                days_31_60=b3160,
                days_61_90=b6190,
                days_90_plus=b90p,
                total=row_total,
            ))
            for k, v in [
                ("current", cur), ("1_30", b130), ("31_60", b3160),
                ("61_90", b6190), ("90_plus", b90p),
            ]:
                totals_d[k] += v

        return AgedReceivablesSummaryResponse(
            currency=currency,
            as_of_date=today,
            totals=AgingBuckets.from_bucket_dict(dict(totals_d)),
            customers=customers,
        )

    def list_aged_receivables_detail(
        self, currency: str, params: PaginationParams
    ) -> AgedReceivablesDetailResponse:
        """Paginated invoice-level aged AR rows."""
        today = datetime.now(UTC).date()
        rows = self.repo.aged_receivables_detail(
            currency, offset=params.offset, limit=params.fetch_limit
        )
        has_next = len(rows) > params.per_page
        items_raw = rows[:params.per_page]
        items = [
            AgedReceivableDetailRow(
                id=r.id,
                customer_name=r.customer_name,
                reference=r.reference,
                number=r.number,
                transaction_date=r.transaction_date,
                due_date=r.due_date,
                status=r.status,
                total_due=Decimal(str(r.total_due)),
                amount_paid=Decimal(str(r.amount_paid)),
                balance_due=Decimal(str(r.balance_due)),
                bucket=r.bucket,
                days_overdue=int(r.days_overdue),
            )
            for r in items_raw
        ]
        metadata = PaginationMetadata(
            page=params.page,
            per_page=params.per_page,
            total=None,
            total_pages=None,
            has_next=has_next,
            has_prev=params.page > 1,
        )
        return AgedReceivablesDetailResponse(
            currency=currency,
            as_of_date=today,
            items=items,
            metadata=metadata,
        )
        

    # Aged Payables

    def get_aged_payables(self, currency: str) -> AgedPayablesSummaryResponse:
        """Pivot flat query rows into per-vendor aging grid."""
        today = datetime.now(UTC).date()
        flat_rows = self.repo.aged_payables_summary(currency)

        # Pivot: vendor_name → {bucket: Decimal}
        pivot: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
        for row in flat_rows:
            pivot[row.vendor_name][row.bucket] += Decimal(str(row.amount))

        totals_d: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        vendors: list[AgedPayableRow] = []

        for vname in sorted(pivot):
            buckets = pivot[vname]
            cur   = buckets.get("current", _ZERO)
            b130  = buckets.get("1_30",    _ZERO)
            b3160 = buckets.get("31_60",   _ZERO)
            b6190 = buckets.get("61_90",   _ZERO)
            b90p  = buckets.get("90_plus", _ZERO)
            row_total = cur + b130 + b3160 + b6190 + b90p
            vendors.append(AgedPayableRow(
                vendor_name=vname,
                current=cur,
                days_1_30=b130,
                days_31_60=b3160,
                days_61_90=b6190,
                days_90_plus=b90p,
                total=row_total,
            ))
            for k, v in [
                ("current", cur), ("1_30", b130), ("31_60", b3160),
                ("61_90", b6190), ("90_plus", b90p),
            ]:
                totals_d[k] += v

        return AgedPayablesSummaryResponse(
            currency=currency,
            as_of_date=today,
            totals=AgingBuckets.from_bucket_dict(dict(totals_d)),
            vendors=vendors,
        )

    def list_aged_payables_detail(
        self, currency: str, params: PaginationParams
    ) -> AgedPayablesDetailResponse:
        """Paginated combined expense + PO aged AP rows."""
        today = datetime.now(UTC).date()
        rows = self.repo.aged_payables_detail(
            currency, offset=params.offset, limit=params.fetch_limit
        )
        has_next = len(rows) > params.per_page
        items_raw = rows[:params.per_page]
        items = [
            AgedPayableDetailRow(
                source_id=r.source_id,
                source_type=r.source_type,
                vendor_name=r.vendor_name,
                reference=r.reference,
                number=r.number,
                entry_date=r.entry_date,
                due_date=r.due_date,
                status=r.status,
                total_due=Decimal(str(r.total_due)),
                amount_paid=Decimal(str(r.amount_paid)),
                balance_due=Decimal(str(r.balance_due)),
                bucket=r.bucket,
                days_overdue=int(r.days_overdue),
            )
            for r in items_raw
        ]
        metadata = PaginationMetadata(
            page=params.page,
            per_page=params.per_page,
            total=None,
            total_pages=None,
            has_next=has_next,
            has_prev=params.page > 1,
        )
        return AgedPayablesDetailResponse(
            currency=currency,
            as_of_date=today,
            items=items,
            metadata=metadata,
        )
