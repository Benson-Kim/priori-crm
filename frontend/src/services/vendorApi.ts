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
import type { PaginatedApiResponse } from "@/lib/types";
import { createSearchParams } from "@/lib/utils";

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

// Supplier Statements cards (PO-33)
//
// Local shapes until the OpenAPI types regenerate; they mirror the backend
// VendorPurchaseOrdersCard / VendorPaymentsCard / VendorInvoicesCard schemas.

export interface VendorPurchaseOrderRow {
  id: string;
  po_reference: string;
  order_date: string;
  amount: string | number;
  currency: string;
  derived_status: "paid" | "pending" | "overpaid";
}

export interface VendorPurchaseOrdersCard {
  rows: VendorPurchaseOrderRow[];
  paid: number;
  pending: number;
  overpaid: number;
}

export interface VendorPaymentRow {
  id: string;
  po_reference: string;
  payment_date: string;
  invoice_number: string | null;
  reference: string | null;
  amount: string | number;
  currency: string;
  has_document: boolean;
}

export interface VendorPaymentsCard {
  rows: VendorPaymentRow[];
  total_amount: string | number;
}

export interface VendorInvoiceRow {
  id: string;
  ref_no: string;
  invoice_date: string;
  amount: string | number;
  balance: string | number;
  status: string;
  currency: string;
}

export interface VendorInvoicesCard {
  rows: VendorInvoiceRow[];
  total_amount: string | number;
}

export interface VendorCardDateRange {
  dateFrom?: string;
  dateTo?: string;
}

function cardParams(range?: VendorCardDateRange): Record<string, string> {
  const params: Record<string, string> = {};
  if (range?.dateFrom) params.dateFrom = range.dateFrom;
  if (range?.dateTo) params.dateTo = range.dateTo;
  return params;
}

export function getVendorPurchaseOrdersCard(
  vendorId: string,
  range?: VendorCardDateRange
): Promise<VendorPurchaseOrdersCard> {
  return apiGet<VendorPurchaseOrdersCard>(
    `vendors/${vendorId}/cards/purchase-orders`,
    cardParams(range)
  );
}

export function getVendorPaymentsCard(
  vendorId: string,
  range?: VendorCardDateRange
): Promise<VendorPaymentsCard> {
  return apiGet<VendorPaymentsCard>(
    `vendors/${vendorId}/cards/payments`,
    cardParams(range)
  );
}

export function getVendorInvoicesCard(
  vendorId: string,
  range?: VendorCardDateRange
): Promise<VendorInvoicesCard> {
  return apiGet<VendorInvoicesCard>(
    `vendors/${vendorId}/cards/invoices`,
    cardParams(range)
  );
}

function cardExportPath(
  vendorId: string,
  resource: "purchase-orders" | "payments" | "invoices",
  range?: VendorCardDateRange
): string {
  const query = createSearchParams(cardParams(range));
  const base = `vendors/${vendorId}/cards/${resource}/export/excel`;
  return query ? `${base}?${query}` : base;
}

export function exportVendorPurchaseOrdersExcel(
  vendorId: string,
  range?: VendorCardDateRange
): Promise<Blob> {
  return apiDownload(cardExportPath(vendorId, "purchase-orders", range));
}

export function exportVendorPaymentsExcel(
  vendorId: string,
  range?: VendorCardDateRange
): Promise<Blob> {
  return apiDownload(cardExportPath(vendorId, "payments", range));
}

export function exportVendorInvoicesExcel(
  vendorId: string,
  range?: VendorCardDateRange
): Promise<Blob> {
  return apiDownload(cardExportPath(vendorId, "invoices", range));
}
