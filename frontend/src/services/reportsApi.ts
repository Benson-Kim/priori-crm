/**
 * Reports API service.
 *
 * All report endpoints under /api/v1/reports/*.
 * Types are hand-declared to match the Pydantic schemas in
 * api/app/modules/reports/schemas.py — no generated types needed.
 *
 * Decimal amounts come back as strings; convert with Number(val) at display time.
 * Period: always sends range=custom + dateFrom + dateTo (from ReportPeriodPicker).
 */

import { apiDownload, apiGet } from "@/lib/api";
import { DEFAULT_CURRENCY } from "@/lib/constants";
import { buildReportPeriodParams, type ReportPeriodFilter } from "@/lib/reportUtils";
import type { PaginatedApiResponse } from "@/lib/types";
import { saveBlob } from "@/lib/utils";


// Shared period resolver

function periodParams(
  period: ReportPeriodFilter,
  currency: string = DEFAULT_CURRENCY
): Record<string, string | number | boolean | undefined> {
  return buildReportPeriodParams(period, currency);
}


// Sales schemas

export interface SalesSummaryMetrics {
  subtotal: string;
  discount_total: string;
  net_revenue: string;
  tax_collected: string;
  total_invoiced: string;
  invoice_count: number;
  outstanding_balance: string;
  overdue_balance: string;
}

export interface RevenueByCustomer {
  customer_id: string;
  customer_name: string;
  invoice_count: number;
  amount: string;
}

export interface RevenueByCategory {
  category: string;
  document_count: number;
  amount: string;
}

export interface SalesReportSummaryResponse {
  period: ResolvedPeriod;
  currency: string;
  metrics: SalesSummaryMetrics;
  revenue_by_customer: RevenueByCustomer[];
  revenue_by_category: RevenueByCategory[];
}

export interface SalesLedgerEntry {
  id: string;
  customer_name: string;
  reference: string;
  number: string;
  date: string;
  status: string;
  currency: string;
  subtotal: string;
  discount: string;
  net_revenue: string;
  amount: string;
  tax: string;
  balance_due: string;
}

export interface SalesStatusCounts {
  all: number;
  sent: number;
  partial: number;
  paid: number;
  overdue: number;
}

// Purchases schemas

export interface PurchasesSummaryMetrics {
  actual_spend: string;
  po_commitments: string;
  input_vat_estimate: string;
  po_commitment_tax: string;
  expense_count: number;
  po_commitment_count: number;
  outstanding_payables: string;
}

export interface SpendByVendor {
  vendor_id: string;
  vendor_name: string;
  amount: string;
}

export interface PurchasesReportSummaryResponse {
  period: ResolvedPeriod;
  currency: string;
  metrics: PurchasesSummaryMetrics;
  spend_by_vendor: SpendByVendor[];
}

export interface PurchasesLedgerEntry {
  source_id: string;
  source_type: "expense" | "purchase_order";
  entity_name: string;
  reference: string;
  number: string;
  category: "expense" | "purchase_order";
  entry_date: string;
  status: string;
  currency: string;
  amount: string;
  tax: string;
  balance_due: string;
}

export interface PurchasesSourceCounts {
  all: number;
  expense: number;
  purchase_order: number;
}

// Tax schemas

export interface TaxSummaryMetrics {
  vat_collected: string;
  input_vat_estimate: string;
  net_vat_estimate: string;
}

export interface TaxByTypeRow {
  tax_type: string;
  tax_rate: string | null;
  tax_amount: string;
  document_count: number;
}

export interface TaxReportResponse {
  period: ResolvedPeriod;
  currency: string;
  report_label: "VAT reconciliation estimate";
  filing_warning: string;
  limitations: string[];
  metrics: TaxSummaryMetrics;
  sales_by_tax_type: TaxByTypeRow[];
  purchases_by_tax_type: TaxByTypeRow[];
}

// Aged AR/AP schemas

export interface AgingBuckets {
  current: string;
  days_1_30: string;
  days_31_60: string;
  days_61_90: string;
  days_90_plus: string;
  total: string;
}

export interface AgedReceivableRow {
  customer_id: string;
  customer_name: string;
  current: string;
  days_1_30: string;
  days_31_60: string;
  days_61_90: string;
  days_90_plus: string;
  total: string;
}

export interface AgedReceivablesSummaryResponse {
  currency: string;
  as_of_date: string;
  totals: AgingBuckets;
  customers: AgedReceivableRow[];
}

export interface AgedReceivableDetailRow {
  id: string;
  customer_name: string;
  reference: string;
  number: string;
  transaction_date: string;
  due_date: string;
  status: string;
  total_due: string;
  amount_paid: string;
  balance_due: string;
  bucket: string;
  days_overdue: number;
}

export interface AgedReceivablesDetailResponse {
  currency: string;
  as_of_date: string;
  items: AgedReceivableDetailRow[];
  metadata: import("@/lib/types").PaginationMetadata;
}

export interface AgedPayableRow {
  vendor_id: string;
  vendor_name: string;
  current: string;
  days_1_30: string;
  days_31_60: string;
  days_61_90: string;
  days_90_plus: string;
  total: string;
}

export interface AgedPayablesSummaryResponse {
  currency: string;
  as_of_date: string;
  totals: AgingBuckets;
  vendors: AgedPayableRow[];
}

// Shared backend period type (in responses)

export interface ResolvedPeriod {
  range: string;
  date_from: string;
  date_to: string;
  previous_date_from: string;
  previous_date_to: string;
}

// Aging utilities

export const AGING_BUCKET_LABELS: Record<string, string> = {
  current: "Current",
  "1_30": "1-30 days",
  "31_60": "31-60 days",
  "61_90": "61-90 days",
  "90_plus": "90+ days",
};

export const AGING_BUCKET_ORDER = [
  "current",
  "1_30",
  "31_60",
  "61_90",
  "90_plus",
] as const;

export type AgingBucketKey = (typeof AGING_BUCKET_ORDER)[number];

/** Format days_overdue for display.
 * Negative = not yet due; 0 = due today; positive = days overdue. */
export function formatDaysOverdue(days: number): string {
  if (days < 0) return `Due in ${Math.abs(days)}d`;
  if (days === 0) return "Due today";
  return `${days}d overdue`;
}

// Tax type labels

export const TAX_TYPE_LABELS: Record<string, string> = {
  vat_16: "16% VAT",
  vat_8: "8% VAT",
  vat_0: "0% VAT (Zero-rated)",
  vat_custom: "Custom VAT",
  exempt: "Exempt",
  no_tax: "No Tax",
};

export function formatTaxTypeLabel(row: TaxByTypeRow): string {
  if (row.tax_type !== "vat_custom") {
    return TAX_TYPE_LABELS[row.tax_type] ?? row.tax_type;
  }
  const rate = (Number(row.tax_rate ?? 0) * 100)
    .toFixed(4)
    .replace(/\.?0+$/, "");
  return `${rate}% VAT (Custom)`;
}

// Sales API

export function getSalesReport(period: ReportPeriodFilter, currency = DEFAULT_CURRENCY) {
  return apiGet<SalesReportSummaryResponse>("reports/sales", periodParams(period, currency));
}

export function getSalesLedger(
  period: ReportPeriodFilter,
  currency = DEFAULT_CURRENCY,
  options: {
    status?: string;
    search?: string;
    page?: number;
    perPage?: number;
    withTotal?: boolean;
  } = {}
) {
  const { status, search, page = 1, perPage = 10, withTotal = true } = options;
  return apiGet<PaginatedApiResponse<SalesLedgerEntry>>("reports/sales/ledger", {
    ...periodParams(period, currency),
    status,
    search,
    page,
    per_page: perPage,
    withTotal,
  });
}

export function getSalesCounts(period: ReportPeriodFilter, currency = DEFAULT_CURRENCY) {
  return apiGet<SalesStatusCounts>("reports/sales/counts", periodParams(period, currency));
}

export async function exportSalesReport(
  period: ReportPeriodFilter,
  currency = DEFAULT_CURRENCY
) {
  const blob = await apiDownload("reports/sales/export", periodParams(period, currency));
  const p = buildReportPeriodParams(period, currency);
  saveBlob(blob, `sales-report-${p.dateFrom}-${p.dateTo}.xlsx`);
}

// Purchases API

export function getPurchasesReport(period: ReportPeriodFilter, currency = DEFAULT_CURRENCY) {
  return apiGet<PurchasesReportSummaryResponse>(
    "reports/purchases",
    periodParams(period, currency)
  );
}

export function getPurchasesLedger(
  period: ReportPeriodFilter,
  currency = DEFAULT_CURRENCY,
  options: {
    source?: "all" | "expense" | "purchase_order";
    search?: string;
    page?: number;
    perPage?: number;
    withTotal?: boolean;
  } = {}
) {
  const { source = "all", search, page = 1, perPage = 10, withTotal = true } = options;
  return apiGet<PaginatedApiResponse<PurchasesLedgerEntry>>("reports/purchases/ledger", {
    ...periodParams(period, currency),
    source,
    search,
    page,
    per_page: perPage,
    withTotal,
  });
}

export function getPurchasesCounts(
  period: ReportPeriodFilter,
  currency = DEFAULT_CURRENCY,
  search?: string
) {
  return apiGet<PurchasesSourceCounts>("reports/purchases/counts", {
    ...periodParams(period, currency),
    search,
  });
}

export async function exportPurchasesReport(
  period: ReportPeriodFilter,
  currency = DEFAULT_CURRENCY
) {
  const blob = await apiDownload(
    "reports/purchases/export",
    periodParams(period, currency)
  );
  const p = buildReportPeriodParams(period, currency);
  saveBlob(blob, `purchases-report-${p.dateFrom}-${p.dateTo}.xlsx`);
}

// Tax API

export function getTaxReport(period: ReportPeriodFilter) {
  // Tax report always KES
  return apiGet<TaxReportResponse>("reports/taxes", periodParams(period, "KES"));
}

export async function exportTaxReport(period: ReportPeriodFilter) {
  const blob = await apiDownload("reports/taxes/export", periodParams(period, "KES"));
  const p = buildReportPeriodParams(period, "KES");
  saveBlob(blob, `tax-report-${p.dateFrom}-${p.dateTo}.xlsx`);
}

// Aged AR/AP API

export function getAgedReceivables(currency = DEFAULT_CURRENCY) {
  return apiGet<AgedReceivablesSummaryResponse>("reports/aged-receivables", { currency });
}

export function getAgedReceivablesDetail(
  currency = DEFAULT_CURRENCY,
  page = 1,
  perPage = 25
) {
  return apiGet<AgedReceivablesDetailResponse>("reports/aged-receivables/detail", {
    currency,
    page,
    per_page: perPage,
  });
}

export function getAgedPayables(currency = DEFAULT_CURRENCY) {
  return apiGet<AgedPayablesSummaryResponse>("reports/aged-payables", { currency });
}
