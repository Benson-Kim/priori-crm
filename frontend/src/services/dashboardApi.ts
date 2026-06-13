/**
 * Dashboard API service.
 *
 * Read-only widgets backing the Dashboard (Overview) page
 * (GET /dashboard/*). Response types come from the generated OpenAPI
 * contract (`@/lib/apiTypes`) rather than hand-mirrored Pydantic schemas,
 * so the frontend can never drift from the backend response shape.
 *
 * Contract notes:
 * - Decimal amounts are serialized as strings; convert with `Number(...)`
 *   at the display edge only.
 * - `change_percent` is null when the previous-period baseline is zero (a
 *   deliberate division-by-zero guard server-side) — render it as a dash,
 *   never as 0%.
 * - The period contract (range / dateFrom / dateTo / currency) is identical
 *   to the statements endpoints; the shared `PeriodFilter` and
 *   `buildPeriodParams` helpers are reused from `statementsApi` so the two
 *   contracts cannot diverge.
 */

import { apiGet } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import { DEFAULT_CURRENCY } from "@/lib/constants";
import {
  buildPeriodParams,
  type PeriodFilter,
} from "@/services/statementsApi";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type DashboardSummary = Schema<"DashboardSummaryResponse">;
export type CashflowSeries = Schema<"CashflowSeriesResponse">;
export type CashflowBucket = Schema<"CashflowBucket">;
export type TopSales = Schema<"TopSalesResponse">;
export type TopSaleLine = Schema<"TopSaleLine">;
export type DashboardTransactions = Schema<"DashboardTransactionsResponse">;
export type DashboardTransaction = Schema<"DashboardTransaction">;
export type Granularity = Schema<"Granularity">;

/** Metric cards: balance / income / expenses / new-customers with deltas. */
export function getDashboardSummary(
  period: PeriodFilter,
  currency: string = DEFAULT_CURRENCY
) {
  return apiGet<DashboardSummary>(
    "dashboard/summary",
    buildPeriodParams(period, currency)
  );
}

/** Zero-filled income/expense buckets for the cashflow chart. */
export function getCashflowSeries(
  period: PeriodFilter,
  currency: string = DEFAULT_CURRENCY,
  granularity?: Granularity
) {
  return apiGet<CashflowSeries>("dashboard/cashflow-series", {
    ...buildPeriodParams(period, currency),
    granularity,
  });
}

/** Top-selling invoice line items by pre-tax amount, descending. */
export function getTopSales(
  period: PeriodFilter,
  currency: string = DEFAULT_CURRENCY,
  limit = 5
) {
  return apiGet<TopSales>("dashboard/top-sales", {
    ...buildPeriodParams(period, currency),
    limit,
  });
}

/** Most recent recognized documents in the signed ledger convention. */
export function getRecentTransactions(
  period: PeriodFilter,
  currency: string = DEFAULT_CURRENCY,
  limit = 10
) {
  return apiGet<DashboardTransactions>("dashboard/transactions", {
    ...buildPeriodParams(period, currency),
    limit,
  });
}
