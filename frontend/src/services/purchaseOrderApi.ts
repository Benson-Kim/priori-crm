/**
 * Purchase Order API service.
 *
 * Response types are sourced from the generated OpenAPI contract
 * (`@/lib/apiTypes`) rather than hand-mirrored Pydantic schemas.
 * Request *payloads* stay hand-written as the camelCase transport shape the
 * API maps onto its snake_case fields via Pydantic aliases.
 */

import {
  apiDelete,
  apiDownload,
  apiGet,
  apiPost,
  apiPut,
  apiUpload,
  flattenPaginated,
} from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import type { PaginatedApiResponse } from "@/lib/types";
import { createSearchParams } from "@/lib/utils";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type PurchaseOrderResponse = Schema<"PurchaseOrderResponse">;
// Two VendorSummary schemas exist (vendors + purchase_orders modules); the
// purchase-order-flavoured one is only ever PurchaseOrderResponse.vendor, so
// derive it from there to avoid the ambiguous bare Schema<"VendorSummary">
// lookup.
export type VendorSummary = PurchaseOrderResponse["vendor"];
export type PurchaseOrderLineItem = Schema<"PurchaseOrderLineItemResponse">;
export type PurchaseOrderDocument = Schema<"PurchaseOrderDocumentResponse">;
export type PurchaseOrderSummary = Schema<"PurchaseOrderSummary">;
export type PurchaseOrderStatusCounts = Schema<"PurchaseOrderStatusCounts">;
export type PurchaseOrderCalculationResponse =
  Schema<"PurchaseOrderCalculationResponse">;
export type PurchaseOrderDuplicateResponse =
  Schema<"PurchaseOrderDuplicateResponse">;
export type PurchaseOrderSendResponse = Schema<"PurchaseOrderSendResponse">;
export type PurchaseOrderPayment = Schema<"PurchaseOrderPaymentResponse">;

// Payloads (hand-written camelCase transport shape).

export interface PurchaseOrderLineItemPayload {
  itemName: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface PaginatedPurchaseOrders {
  items: PurchaseOrderSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface PurchaseOrderCreatePayload {
  vendorId: string;
  orderDate: string;
  deliveryDate?: string | null;
  currency?: string;
  isRecurring?: boolean;
  lineItems: PurchaseOrderLineItemPayload[];
  notes?: string | null;
  complianceRef?: string | null;
  termsAndConditions?: string | null;
}

export interface PurchaseOrderUpdatePayload {
  vendorId?: string;
  orderDate?: string;
  deliveryDate?: string | null;
  isRecurring?: boolean;
  lineItems?: PurchaseOrderLineItemPayload[];
  complianceRef?: string | null;
  notes?: string | null;
  termsAndConditions?: string | null;
}

export interface PurchaseOrderSendPayload {
  toEmail?: string | null;
  subject?: string | null;
  body?: string | null;
  attachPdf?: boolean;
}

export interface PurchaseOrderPaymentPayload {
  amount: number;
  paymentDate: string;
  reference?: string | null;
  notes?: string | null;
  /** Optional proof-of-payment document UUID (must belong to the same PO). */
  documentId?: string | null;
}

export interface PurchaseOrderListParams {
  [key: string]: string | number | boolean | undefined;
  page?: number;
  per_page?: number;
  status?: string;
  vendorId?: string;
  dateFrom?: string;
  dateTo?: string;
  deliveryDateFrom?: string;
  deliveryDateTo?: string;
  search?: string;
  withTotal?: boolean;
}

// API Functions

export async function getPurchaseOrders(
  params?: PurchaseOrderListParams
): Promise<PaginatedPurchaseOrders> {
  const raw = await apiGet<PaginatedApiResponse<PurchaseOrderSummary>>(
    "purchase-orders",
    params
  );
  return flattenPaginated(raw);
}

export function getPurchaseOrder(id: string) {
  return apiGet<PurchaseOrderResponse>(`purchase-orders/${id}`);
}

export function getPurchaseOrderByNumber(poNumber: string) {
  return apiGet<PurchaseOrderResponse>(
    `purchase-orders/number/${encodeURIComponent(poNumber)}`
  );
}

export function createPurchaseOrder(data: PurchaseOrderCreatePayload) {
  return apiPost<PurchaseOrderResponse>("purchase-orders", data);
}

export function updatePurchaseOrder(
  id: string,
  data: PurchaseOrderUpdatePayload,
  expectedVersion?: number
) {
  const path =
    expectedVersion != null
      ? `purchase-orders/${id}?expectedVersion=${expectedVersion}`
      : `purchase-orders/${id}`;
  return apiPut<PurchaseOrderResponse>(path, data);
}

export function deletePurchaseOrder(id: string) {
  return apiDelete<void>(`purchase-orders/${id}`);
}

export function getPurchaseOrderCounts() {
  return apiGet<PurchaseOrderStatusCounts>("purchase-orders/counts");
}

/**
 * Send a purchase order to its vendor by email (Draft -> Sent). The recipient
 * defaults to the vendor email server-side when `toEmail` is omitted.
 */
export function sendPurchaseOrder(id: string, data?: PurchaseOrderSendPayload) {
  return apiPost<PurchaseOrderSendResponse>(
    `purchase-orders/${id}/send`,
    data || {}
  );
}

/** Duplicate a purchase order as a new DRAFT; returns the new id/reference. */
export function duplicatePurchaseOrder(id: string) {
  return apiPost<PurchaseOrderDuplicateResponse>(
    `purchase-orders/${id}/duplicate`,
    {}
  );
}

/**
 * Mark a DRAFT purchase order as SENT WITHOUT sending an email (PO-20).
 * For when the PO was sent to the vendor offline.
 */
export function markAsSentPurchaseOrder(id: string) {
  return apiPost<PurchaseOrderResponse>(
    `purchase-orders/${id}/mark-as-sent`,
    {}
  );
}

/**
 * Record a payment against a SENT purchase order (PO-19). When the balance
 * clears, the PO is settled to PAID server-side.
 */
export function recordPurchaseOrderPayment(
  id: string,
  data: PurchaseOrderPaymentPayload
) {
  return apiPost<PurchaseOrderPayment>(`purchase-orders/${id}/payments`, data);
}

/**
 * Download the ORIGINAL purchase-order PDF (full amount, no payments) as a
 * Blob (caller saves with `saveBlob`).
 */
export function downloadPurchaseOrderPdf(id: string): Promise<Blob> {
  return apiDownload(`purchase-orders/${id}/pdf`);
}

/**
 * Download the CURRENT (balance-aware) purchase-order PDF: the same document
 * with Amount Paid + Balance Due rows reflecting payments recorded so far.
 */
export function downloadPurchaseOrderStatementPdf(id: string): Promise<Blob> {
  return apiDownload(`purchase-orders/${id}/pdf/statement`);
}

export async function uploadPurchaseOrderDocument(
  id: string,
  file: File,
  source: "form" | "view" = "view"
): Promise<PurchaseOrderDocument> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("source", source);
  return apiUpload<PurchaseOrderDocument>(
    `purchase-orders/${id}/documents`,
    formData
  );
}

export function getPurchaseOrderDocuments(id: string) {
  return apiGet<PurchaseOrderDocument[]>(`purchase-orders/${id}/documents`);
}

export function deletePurchaseOrderDocument(id: string, documentId: string) {
  return apiDelete<void>(`purchase-orders/${id}/documents/${documentId}`);
}

export function downloadPurchaseOrderDocument(
  id: string,
  documentId: string
): Promise<Blob> {
  return apiDownload(`purchase-orders/${id}/documents/${documentId}/download`);
}

/**
 * Preview purchase order totals. Posts the camelCase
 * PurchaseOrderLineItemPayload shape (the documented POST
 * /purchase-orders/calculate contract), not snake_case keys relying on the
 * backend's populate_by_name fallback.
 */
export function calculateTotals(data: PurchaseOrderLineItemPayload[]) {
  return apiPost<PurchaseOrderCalculationResponse>(
    "purchase-orders/calculate",
    data
  );
}

/**
 * Download the currently-filtered purchase orders as an .xlsx workbook.
 * Returns a Blob the caller saves with `saveBlob`.
 */
export function exportPurchaseOrdersExcel(params?: {
  status?: string;
  vendorId?: string;
  dateFrom?: string;
  dateTo?: string;
  deliveryDateFrom?: string;
  deliveryDateTo?: string;
  search?: string;
  includeLineItems?: boolean;
}): Promise<Blob> {
  const query = params ? createSearchParams(params) : "";
  const path = query
    ? `purchase-orders/export/excel?${query}`
    : "purchase-orders/export/excel";
  return apiDownload(path);
}
