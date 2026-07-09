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
import { toISODateString } from "@/lib/dateUtils";
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

export function isValidRangePreset(
  value: string | null
): value is RangePreset {
  return value != null && (RANGE_PRESETS as readonly string[]).includes(value);
}

/**
 * Whether the period can be sent to the API. Presets are always ready; a
 * custom range needs both dates, in order. Checking here avoids firing
 * requests that are guaranteed to 400 while the user is mid-selection.
 */
export function isPeriodReady(period: PeriodFilter): boolean {
  if (period.range !== "custom") return true;
  return Boolean(
    period.dateFrom && period.dateTo && period.dateFrom <= period.dateTo
  );
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

/**
 * Resolve a PeriodFilter preset to concrete {start, end} ISO dates.
 * Mirrors the RANGE_PRESETS set; `undefined` means "no filter" so the backend
 * applies its own default (last 12 months).
 */
export function resolvePeriod(
  period: PeriodFilter
): { start?: string; end?: string } {
  const now = new Date();
  const end = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  );

  const startOf = (d: Date) => toISODateString(d);
  const endStr = toISODateString(end);

  switch (period.range) {
    case "last_7_days": {
      const s = new Date(end);
      s.setUTCDate(s.getUTCDate() - 6);
      return { start: startOf(s), end: endStr };
    }
    case "this_month": {
      const s = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 1));
      return { start: startOf(s), end: endStr };
    }
    case "last_month": {
      const s = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - 1, 1));
      const e = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 0));
      return { start: startOf(s), end: toISODateString(e) };
    }
    case "this_quarter": {
      const q = Math.floor(end.getUTCMonth() / 3);
      const s = new Date(Date.UTC(end.getUTCFullYear(), q * 3, 1));
      return { start: startOf(s), end: endStr };
    }
    case "this_year": {
      const s = new Date(Date.UTC(end.getUTCFullYear(), 0, 1));
      return { start: startOf(s), end: endStr };
    }
    case "custom":
      return { start: period.dateFrom, end: period.dateTo };
    default:
      return {};
  }
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
