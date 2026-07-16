/**
 * Vendor API service.
 *
 * Response types are sourced from the generated OpenAPI contract
 * (`@/lib/apiTypes`) instead of hand-mirrored Pydantic schemas — the
 * hand-written copies were the root cause of the `vendor.phone` /
 * transaction-column drift. Request *payloads* stay hand-written as the
 * camelCase transport shape the API maps onto its snake_case fields.
 */

import {
  apiDelete,
  apiDownload,
  apiGet,
  apiPost,
  apiPut,
  flattenPaginated,
} from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import { buildStatementParams } from "@/lib/dateUtils";
import type { PaginatedApiResponse } from "@/lib/types";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type VendorStatement = Schema<"VendorStatement">;
export type Vendor = Schema<"VendorResponse">;
export type VendorSummary = Schema<"app__modules__vendors__schemas__VendorSummary">;
export type VendorStatusCounts = Schema<"VendorStatusCounts">;
export type VendorPayablesSummary = Schema<"VendorPayablesSummary">;
export type VendorTransactionSummary = Schema<"VendorTransactionSummary">;
export type DeleteResult = Schema<"VendorDeleteResponse">;
export type DuplicateCheckResult = Schema<"VendorDuplicateCheckResponse">;
export type ContactSearchResult = Schema<"ContactSearchResult">;
export type ContactSearchResponse = Schema<"ContactSearchResponse">;

export interface PaginatedVendors {
  items: VendorSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface PaginatedVendorTransactions {
  items: VendorTransactionSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

// API Functions

export function createVendor(data: Record<string, unknown>) {
  return apiPost<Vendor>("vendors", data);
}

export async function getVendors(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
}): Promise<PaginatedVendors> {
  const raw = await apiGet<PaginatedApiResponse<VendorSummary>>("vendors", params);
  return flattenPaginated(raw);
}

export function getVendor(id: string) {
  // Uses GET /{vendor_id}
  return apiGet<Vendor>(`vendors/${id}`);
}

export function updateVendor(id: string, data: Record<string, unknown>) {
  return apiPut<Vendor>(`vendors/${id}`, data);
}

export function getVendorCounts() {
  return apiGet<VendorStatusCounts>("vendors/counts");
}

export function activateVendor(id: string) {
  return apiPost<Vendor>(`vendors/${id}/activate`, {});
}

export function deactivateVendor(id: string) {
  return apiPost<Vendor>(`vendors/${id}/deactivate`, {});
}

export function deleteVendor(id: string) {
  return apiDelete<DeleteResult>(`vendors/${id}`);
}

export async function getVendorTransactions(
  id: string,
  params?: {
    page?: number;
    per_page?: number;
    status?: string;
    // Source filter: 'expense' | 'purchase_order'. Omitted = all sources.
    type?: string;
  }
): Promise<PaginatedVendorTransactions> {
  const raw = await apiGet<PaginatedApiResponse<VendorTransactionSummary>>(`vendors/${id}/transactions`, params);
  return flattenPaginated(raw);
}

export function getVendorPayables(id: string) {
  return apiGet<VendorPayablesSummary>(`vendors/${id}/payables`);
}

export function searchContacts(q: string, limit = 20) {
  return apiGet<ContactSearchResponse>("vendors/contacts/search", { q, limit });
}

export function checkEmailDuplicate(email: string, excludeVendorId?: string) {
  const params: Record<string, string> = { email };
  if (excludeVendorId) params.excludeVendorId = excludeVendorId;
  return apiGet<DuplicateCheckResult>("vendors/check-email", params);
}

export async function getVendorStatement(
  vendorId: string,
  periodStart?: string,
  periodEnd?: string
): Promise<VendorStatement> {
  const params: Record<string, string> = {};
  if (periodStart) params.period_start = periodStart;
  if (periodEnd) params.period_end = periodEnd;

  return apiGet<VendorStatement>(`vendors/${vendorId}/statement`, params);
}

// Vendor detail cards (Total POs / Total Payments / Total Bills)
//
// NOTE: hand-written until the OpenAPI schema is regenerated to include the
// new VendorCardSummary/VendorCardItem models — same temporary convention as
// the PO-VAT `readPoVat` cast. Regenerate the contract and switch these to
// `Schema<"VendorCardSummary">` to drop the local copies.

export type VendorCardKey = "purchase-orders" | "payments" | "bills";

export interface VendorCardItem {
  id: string;
  source: "purchase_order" | "bill" | "po_payment" | "expense_payment";
  ref_no: string;
  date: string;
  amount: string;
  payment_state: "paid" | "pending";
  /** Payment-only columns (null on PO/bill rows). */
  invoice_number?: string | null;
  payment_ref?: string | null;
  parent_id?: string | null;
}

export interface VendorCardSummary {
  total: string;
  paid_total: string;
  pending_total: string;
  count: number;
  currency: string;
  period_start: string | null;
  period_end: string | null;
  page: number;
  per_page: number;
  total_pages: number;
  items: VendorCardItem[];
}

/** Endpoint segment for each card (kept in one place). */
const CARD_PATH: Record<VendorCardKey, string> = {
  "purchase-orders": "purchase-orders",
  payments: "payments",
  bills: "bills",
};

export function getVendorCard(
  vendorId: string,
  card: VendorCardKey,
  params?: {
    period_start?: string;
    period_end?: string;
    page?: number;
    per_page?: number;
  }
): Promise<VendorCardSummary> {
  return apiGet<VendorCardSummary>(
    `vendors/${vendorId}/cards/${CARD_PATH[card]}`,
    params
  );
}

export function exportVendorCardExcel(
  vendorId: string,
  card: VendorCardKey,
  params?: { period_start?: string; period_end?: string }
): Promise<Blob> {
  return apiDownload(
    `vendors/${vendorId}/cards/${CARD_PATH[card]}/export/excel`,
    params
  );
}

export function exportVendorCardPdf(
  vendorId: string,
  card: VendorCardKey,
  params?: { period_start?: string; period_end?: string }
): Promise<Blob> {
  return apiDownload(
    `vendors/${vendorId}/cards/${CARD_PATH[card]}/export/pdf`,
    params
  );
}


export function downloadVendorStatementPdf(
  vendorId: string, periodStart?: string, periodEnd?: string
): Promise<Blob> {
  return apiDownload(
    `vendors/${vendorId}/statement/pdf`,
    buildStatementParams(periodStart, periodEnd)
  );
}

export function exportVendorStatementExcel(
  vendorId: string, periodStart?: string, periodEnd?: string
): Promise<Blob> {
  return apiDownload(
    `vendors/${vendorId}/statement/excel`,
    buildStatementParams(periodStart, periodEnd)
  );
}
