"""Reports service — orchestration layer over the read-side SQL.

All heavy lifting (predicates, aggregation) lives in
``app.modules.reports.queries``; this layer pivots query results into
typed Pydantic response models.

Read-only: never flushes or commits.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.common.exceptions import BadRequestException, ValidationException
from app.common.pagination import (
    PaginatedResponse,
    PaginationMetadata,
    PaginationParams,
)
from app.constants.enums import Currency
from app.lib.config import settings
from app.modules.reports.queries import ReportsRepository
from app.modules.reports.schemas import (
    VAT_FILING_WARNING,
    VAT_RECONCILIATION_LABEL,
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
    ExcludedTaxTransaction,
    PurchasesLedgerEntry,
    PurchasesReportSummaryResponse,
    PurchasesSourceCounts,
    PurchasesSummaryMetrics,
    ReportCompleteness,
    RevenueByCategory,
    RevenueByCustomer,
    SalesLedgerEntry,
    SalesReportSummaryResponse,
    SalesStatusCounts,
    SalesSummaryMetrics,
    SpendByVendor,
    TaxByTypeRow,
    TaxReportResponse,
    TaxSummaryMetrics,
)
from app.modules.statements.schemas import ResolvedPeriod

logger = logging.getLogger(__name__)

_ZERO = Decimal("0.00")
_MISSING_TAX_POINT_FX_REASON = (
    "Historical KES tax-point conversion values are not stored"
)


class ReportsService:
    """Read-only reporting service — never mutates state."""

    def __init__(self, db: Session, current_user=None) -> None:
        self.db = db
        self.current_user = current_user
        self.repo = ReportsRepository(db)

    @staticmethod
    def validate_export_size(
        row_count: int,
        report_name: str,
        *,
        max_rows: int | None = None,
    ) -> None:
        """Reject an oversized synchronous export before or during generation."""
        limit = settings.TAX_REPORT_EXPORT_MAX_ROWS if max_rows is None else max_rows
        if row_count > limit:
            raise BadRequestException(
                f"{report_name} report has more than {limit:,} rows; narrow the "
                "reporting period before exporting"
            )

    # Sales: Summary

    def get_sales_summary(
        self, period: ResolvedPeriod, currency: Currency
    ) -> SalesReportSummaryResponse:
        """Overview metrics + top-customer + category breakdowns."""
        summary = self.repo.sales_summary(period.date_from, period.date_to, currency)
        cust_rows = self.repo.revenue_by_customer(
            period.date_from, period.date_to, currency
        )
        cat_rows = self.repo.revenue_by_category(
            period.date_from, period.date_to, currency
        )

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
                    customer_id=r.customer_id,
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
                period.date_from,
                period.date_to,
                currency,
                search=search,
                status=status,
            )
        rows = self.repo.sales_ledger(
            period.date_from,
            period.date_to,
            currency,
            search=search,
            status=status,
            offset=params.offset,
            limit=params.fetch_limit,
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

    def export_sales_ledger(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        *,
        batch_size: int,
    ) -> Iterator[SalesLedgerEntry]:
        """Yield the complete sales ledger without pagination or truncation."""
        rows = self.repo.iter_sales_ledger(
            period.date_from,
            period.date_to,
            currency,
            batch_size=batch_size,
        )
        for row_number, row in enumerate(rows, start=1):
            self.validate_export_size(row_number, "Sales")
            yield SalesLedgerEntry(
                id=row.id,
                customer_name=row.customer_name,
                reference=row.reference,
                number=row.number,
                date=row.date,
                status=row.status,
                currency=row.currency,
                subtotal=Decimal(str(row.subtotal)),
                discount=Decimal(str(row.discount)),
                net_revenue=Decimal(str(row.net_revenue)),
                amount=Decimal(str(row.amount)),
                tax=Decimal(str(row.tax)),
                balance_due=Decimal(str(row.balance_due)),
            )

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
            period.date_from,
            period.date_to,
            currency,
            search=search,
            status=status,
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
        summary = self.repo.purchases_summary(
            period.date_from, period.date_to, currency
        )
        vendor_rows = self.repo.spend_by_vendor(
            period.date_from, period.date_to, currency
        )

        return PurchasesReportSummaryResponse(
            period=period,
            currency=currency,
            metrics=PurchasesSummaryMetrics(
                actual_spend=summary.actual_spend,
                po_commitments=summary.po_commitments,
                input_vat_estimate=summary.input_vat_estimate,
                po_commitment_tax=summary.po_commitment_tax,
                expense_count=summary.expense_count,
                po_commitment_count=summary.po_commitment_count,
                outstanding_payables=summary.outstanding_payables,
            ),
            spend_by_vendor=[
                SpendByVendor(
                    vendor_id=r.vendor_id,
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
                period.date_from,
                period.date_to,
                currency,
                source=source,
                search=search,
            )
        rows = self.repo.purchases_ledger(
            period.date_from,
            period.date_to,
            currency,
            source=source,
            search=search,
            offset=params.offset,
            limit=params.fetch_limit,
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

    def export_purchases_ledger(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        *,
        batch_size: int,
    ) -> Iterator[PurchasesLedgerEntry]:
        """Yield the complete purchases ledger without pagination or truncation."""
        rows = self.repo.iter_purchases_ledger(
            period.date_from,
            period.date_to,
            currency,
            batch_size=batch_size,
        )
        for row_number, row in enumerate(rows, start=1):
            self.validate_export_size(row_number, "Purchases")
            yield PurchasesLedgerEntry(
                source_id=row.source_id,
                source_type=row.source_type,
                entity_name=row.entity_name,
                reference=row.reference,
                number=row.number,
                category=row.category,
                entry_date=row.entry_date,
                status=row.status,
                currency=row.currency,
                amount=Decimal(str(row.amount)),
                tax=Decimal(str(row.tax)),
                balance_due=Decimal(str(row.balance_due)),
            )

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
            period.date_from,
            period.date_to,
            currency,
            source=source,
            search=search,
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
        self,
        period: ResolvedPeriod,
        currency: Currency,
        *,
        strict: bool = False,
    ) -> TaxReportResponse:
        """Build the supported KES estimate and disclose unsupported documents."""
        excluded_count, excluded_currencies = self.repo.foreign_currency_vat_summary(
            period.date_from, period.date_to
        )
        if strict and excluded_count:
            raise ValidationException(
                detail=(
                    "Strict VAT reconciliation requires every foreign-currency VAT "
                    "document to have immutable KES tax-point conversion evidence."
                ),
                errors=[
                    {
                        "reason": "missing_tax_point_fx",
                        "excluded_document_count": excluded_count,
                        "excluded_currencies": excluded_currencies,
                    }
                ],
            )
        tax_s = self.repo.tax_summary(period.date_from, period.date_to, currency)
        sales_rows = self.repo.tax_by_type_sales(
            period.date_from, period.date_to, currency
        )
        purch_rows = self.repo.tax_by_type_purchases(
            period.date_from, period.date_to, currency
        )

        return TaxReportResponse(
            period=period,
            currency=currency,
            report_label=VAT_RECONCILIATION_LABEL,
            filing_warning=VAT_FILING_WARNING,
            completeness=ReportCompleteness(
                status="partial" if excluded_count else "complete",
                excluded_document_count=excluded_count,
                excluded_currencies=excluded_currencies,
                reasons=([_MISSING_TAX_POINT_FX_REASON] if excluded_count else []),
            ),
            limitations=[
                "No eTIMS or customs-record matching is performed.",
                "Expense VAT is not evidence-qualified for input-tax eligibility.",
                (
                    "Foreign-currency VAT without persisted KES tax-point values is "
                    "excluded, disclosed, and available through the excluded-items view."
                ),
                (
                    "Credit/debit notes, reverse VAT, withholding VAT, and "
                    "apportionment are not reconciled."
                ),
            ],
            metrics=TaxSummaryMetrics(
                vat_collected=tax_s.vat_collected,
                input_vat_estimate=tax_s.input_vat_estimate,
                net_vat_estimate=tax_s.net_vat_estimate,
            ),
            sales_by_tax_type=[
                TaxByTypeRow(
                    tax_type=r.tax_type,
                    tax_rate=(
                        Decimal(str(r.tax_rate)) if r.tax_rate is not None else None
                    ),
                    taxable_value=Decimal(str(r.taxable_value)),
                    tax_amount=Decimal(str(r.tax_amount)),
                    document_count=int(r.document_count),
                )
                for r in sales_rows
            ],
            purchases_by_tax_type=[
                TaxByTypeRow(
                    tax_type=r.tax_type,
                    tax_rate=(
                        Decimal(str(r.tax_rate)) if r.tax_rate is not None else None
                    ),
                    taxable_value=Decimal(str(r.taxable_value)),
                    tax_amount=Decimal(str(r.tax_amount)),
                    document_count=int(r.document_count),
                )
                for r in purch_rows
            ],
        )

    @staticmethod
    def _excluded_tax_transaction(row) -> ExcludedTaxTransaction:
        return ExcludedTaxTransaction(
            document_id=row.document_id,
            document_type=row.document_type,
            reference=row.reference,
            number=row.number,
            transaction_date=row.transaction_date,
            currency=str(row.currency),
            original_amount=Decimal(str(row.original_amount)),
            original_vat_amount=Decimal(str(row.original_vat_amount)),
            reason=_MISSING_TAX_POINT_FX_REASON,
        )

    def list_excluded_tax_transactions(
        self,
        period: ResolvedPeriod,
        params: PaginationParams,
    ) -> PaginatedResponse[ExcludedTaxTransaction]:
        """Paginate unsupported foreign-currency VAT documents for review."""
        total, _ = self.repo.foreign_currency_vat_summary(
            period.date_from, period.date_to
        )
        rows = self.repo.foreign_currency_vat_transactions(
            period.date_from,
            period.date_to,
            offset=params.offset,
            limit=params.fetch_limit,
        )
        items = [self._excluded_tax_transaction(row) for row in rows]
        return PaginatedResponse.create_from_window(items, params, total=total)

    def iter_excluded_tax_transactions(
        self,
        period: ResolvedPeriod,
        *,
        batch_size: int,
    ) -> Iterator[ExcludedTaxTransaction]:
        """Stream unsupported VAT documents for the workbook appendix."""
        for row in self.repo.iter_foreign_currency_vat_transactions(
            period.date_from,
            period.date_to,
            batch_size=batch_size,
        ):
            yield self._excluded_tax_transaction(row)

    # Aged Receivables

    def get_aged_receivables(
        self, currency: str, as_of_date: date
    ) -> AgedReceivablesSummaryResponse:
        """Pivot flat query rows into per-customer aging grid."""
        flat_rows = self.repo.aged_receivables_summary(currency, as_of_date)

        # Pivot by stable identity; duplicate display names without separate rows
        pivot: dict[UUID, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: _ZERO)
        )
        names: dict[UUID, str] = {}
        for row in flat_rows:
            customer_id = row.customer_id
            names[customer_id] = row.customer_name
            pivot[customer_id][row.bucket] += Decimal(str(row.amount))

        totals_d: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        customers: list[AgedReceivableRow] = []

        ordered_ids = sorted(
            pivot, key=lambda value: (names[value].casefold(), str(value))
        )
        for customer_id in ordered_ids:
            buckets = pivot[customer_id]
            cur = buckets.get("current", _ZERO)
            b130 = buckets.get("1_30", _ZERO)
            b3160 = buckets.get("31_60", _ZERO)
            b6190 = buckets.get("61_90", _ZERO)
            b90p = buckets.get("90_plus", _ZERO)
            row_total = cur + b130 + b3160 + b6190 + b90p
            customers.append(
                AgedReceivableRow(
                    customer_id=customer_id,
                    customer_name=names[customer_id],
                    current=cur,
                    days_1_30=b130,
                    days_31_60=b3160,
                    days_61_90=b6190,
                    days_90_plus=b90p,
                    total=row_total,
                )
            )
            for k, v in [
                ("current", cur),
                ("1_30", b130),
                ("31_60", b3160),
                ("61_90", b6190),
                ("90_plus", b90p),
            ]:
                totals_d[k] += v

        return AgedReceivablesSummaryResponse(
            currency=currency,
            as_of_date=as_of_date,
            totals=AgingBuckets.from_bucket_dict(dict(totals_d)),
            customers=customers,
        )

    def list_aged_receivables_detail(
        self, currency: str, as_of_date: date, params: PaginationParams
    ) -> AgedReceivablesDetailResponse:
        """Paginated invoice-level aged AR rows."""
        rows = self.repo.aged_receivables_detail(
            currency, as_of_date, offset=params.offset, limit=params.fetch_limit
        )
        has_next = len(rows) > params.per_page
        items_raw = rows[: params.per_page]
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
            as_of_date=as_of_date,
            items=items,
            metadata=metadata,
        )

    # Aged Payables

    def get_aged_payables(
        self, currency: str, as_of_date: date
    ) -> AgedPayablesSummaryResponse:
        """Pivot flat query rows into per-vendor aging grid."""
        flat_rows = self.repo.aged_payables_summary(currency, as_of_date)

        # Pivot by stable vendor identity (avoid duplicate rows from name changes)
        pivot: dict[UUID, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: _ZERO)
        )
        names: dict[UUID, str] = {}
        for row in flat_rows:
            vendor_id = row.vendor_id
            names[vendor_id] = row.vendor_name
            pivot[vendor_id][row.bucket] += Decimal(str(row.amount))

        totals_d: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        vendors: list[AgedPayableRow] = []

        ordered_ids = sorted(
            pivot, key=lambda value: (names[value].casefold(), str(value))
        )
        for vendor_id in ordered_ids:
            buckets = pivot[vendor_id]
            cur = buckets.get("current", _ZERO)
            b130 = buckets.get("1_30", _ZERO)
            b3160 = buckets.get("31_60", _ZERO)
            b6190 = buckets.get("61_90", _ZERO)
            b90p = buckets.get("90_plus", _ZERO)
            row_total = cur + b130 + b3160 + b6190 + b90p
            vendors.append(
                AgedPayableRow(
                    vendor_id=vendor_id,
                    vendor_name=names[vendor_id],
                    current=cur,
                    days_1_30=b130,
                    days_31_60=b3160,
                    days_61_90=b6190,
                    days_90_plus=b90p,
                    total=row_total,
                )
            )
            for k, v in [
                ("current", cur),
                ("1_30", b130),
                ("31_60", b3160),
                ("61_90", b6190),
                ("90_plus", b90p),
            ]:
                totals_d[k] += v

        return AgedPayablesSummaryResponse(
            currency=currency,
            as_of_date=as_of_date,
            totals=AgingBuckets.from_bucket_dict(dict(totals_d)),
            vendors=vendors,
        )

    def list_aged_payables_detail(
        self, currency: str, as_of_date: date, params: PaginationParams
    ) -> AgedPayablesDetailResponse:
        """Paginated expense aged AP rows."""
        rows = self.repo.aged_payables_detail(
            currency, as_of_date, offset=params.offset, limit=params.fetch_limit
        )
        has_next = len(rows) > params.per_page
        items_raw = rows[: params.per_page]
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
            as_of_date=as_of_date,
            items=items,
            metadata=metadata,
        )
