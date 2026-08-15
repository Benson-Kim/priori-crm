/**
 * Financial statements API service.
 *
 * Read-only endpoints backing the Income Statement and Cashflow pages
 * (GET /statements/*). Response types come from the generated OpenAPI
 * contract (`@/lib/apiTypes`) rather than hand-mirrored Pydantic schemas.
 *
 * Contract notes:
 * - Decimal amounts are serialized as strings; convert with `Number(...)`
 *   at the display edge only.
 * - `change_percent` / margin fields are null when the baseline is zero
 *   (a deliberate division-by-zero guard server-side) — render them as a
 *   dash, never as 0%.
 * - All aggregation is single-currency; `currency` defaults to KES on
 *   both ends.
 */

import { apiGet } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import { DEFAULT_CURRENCY } from "@/lib/constants";
import type { PaginatedApiResponse } from "@/lib/types";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type StatementOverview = Schema<"StatementOverviewResponse">;
export type IncomeStatement = Schema<"IncomeStatementResponse">;
export type IncomeStatementLine = Schema<"IncomeStatementLine">;
export type CashflowEntry = Schema<"CashflowEntry">;
export type CashflowCounts = Schema<"CashflowCounts">;

export type CashflowCategory = "all" | "income" | "expense";

export const RANGE_PRESETS = [
  "last_7_days",
  "this_month",
  "last_month",
  "this_quarter",
  "this_year",
  "last_12_months",
  "custom",
] as const;

export type RangePreset = (typeof RANGE_PRESETS)[number];

export interface PeriodFilter {
  range: RangePreset;
  /** YYYY-MM-DD; only meaningful (and only sent) when range === "custom". */
  dateFrom?: string;
  /** YYYY-MM-DD; only meaningful (and only sent) when range === "custom". */
  dateTo?: string;
}

/**
 * Build the wire query params for a period + currency.
 *
 * The API rejects dateFrom/dateTo alongside non-custom presets (explicit
 * 400 rather than silently ignored input), so strip them here once instead
 * of relying on every caller to remember. Exported so the dashboard service
 * reuses the exact same period contract (single source of truth).
 */
export function buildPeriodParams(period: PeriodFilter, currency: string) {
  return {
    range: period.range,
    dateFrom: period.range === "custom" ? period.dateFrom : undefined,
    dateTo: period.range === "custom" ? period.dateTo : undefined,
    currency,
  };
}

/** Overview cards: revenue / expenses / net profit / margin with deltas. */
export function getStatementOverview(
  period: PeriodFilter,
  currency: string = DEFAULT_CURRENCY
) {
  return apiGet<StatementOverview>(
    "statements/overview",
    buildPeriodParams(period, currency)
  );
}

/** Operating revenue/expense lines grouped by account category. */
export function getIncomeStatement(
  period: PeriodFilter,
  currency: string = DEFAULT_CURRENCY
) {
  return apiGet<IncomeStatement>(
    "statements/income-statement",
    buildPeriodParams(period, currency)
  );
}

export interface CashflowListParams {
  period: PeriodFilter;
  currency?: string;
  category?: CashflowCategory;
  /** Drill-down filter carried over from an income-statement row click. */
  accountCategory?: string;
  search?: string;
  page?: number;
  perPage?: number;
}

/**
 * Paginated signed ledger (income positive, expense negative).
 *
 * `withTotal=true` is requested because the page-number Pagination UI
 * needs total_pages; the backend keeps the COUNT opt-in so cheaper
 * consumers can skip it.
 */
export function getCashflow({
  period,
  currency = DEFAULT_CURRENCY,
  category = "all",
  accountCategory,
  search,
  page = 1,
  perPage = 10,
}: CashflowListParams) {
  return apiGet<PaginatedApiResponse<CashflowEntry>>("statements/cashflow", {
    ...buildPeriodParams(period, currency),
    category,
    accountCategory,
    search,
    page,
    per_page: perPage,
    withTotal: true,
  });
}

export interface CashflowCountsParams {
  period: PeriodFilter;
  currency?: string;
  accountCategory?: string;
  search?: string;
}

/**
 * Filter-tab counts. Deliberately a separate endpoint so the list query
 * never pays a mandatory COUNT (mirrors the backend's ISSUE-016 pattern).
 */
export function getCashflowCounts({
  period,
  currency = DEFAULT_CURRENCY,
  accountCategory,
  search,
}: CashflowCountsParams) {
  return apiGet<CashflowCounts>("statements/cashflow/counts", {
    ...buildPeriodParams(period, currency),
    accountCategory,
    search,
  });
}
