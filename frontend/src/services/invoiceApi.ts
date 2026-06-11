/**
 * Invoice API service.
 *
 * Response types are sourced from the generated OpenAPI contract
 * (`@/lib/apiTypes`) rather than hand-mirrored Pydantic schemas (#41).
 * Request *payloads* stay hand-written as the camelCase transport shape.
 */

import { apiDownload, apiGet, apiPost, apiPut, flattenPaginated } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import type { CurrencyOption } from "@/lib/constants";
import type { PaginatedApiResponse, PaginatedResult } from "@/lib/types";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type InvoiceLineItem = Schema<"InvoiceLineItemResponse">;
export type Payment = Schema<"PaymentResponse">;
export type InvoiceCustomer = Schema<"CustomerSummary">;
export type InvoiceSummary = Schema<"InvoiceSummary">;
export type InvoiceResponse = Schema<"InvoiceResponse">;
export type InvoiceStatusCounts = Schema<"InvoiceStatusCounts">;
export type InvoiceDuplicateResponse = Schema<"InvoiceDuplicateResponse">;
export type InvoiceSendResult = Schema<"InvoiceSendResponse">;

export interface LineItemPayload {
  itemName?: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface InvoiceCreatePayload {
  customerId: string;
  transactionDate: string;
  dueDate: string;
  currency: CurrencyOption;
  lineItems: LineItemPayload[];
  rfqNumber?: string;
  notes?: string;
  discountType?: string;
  discountAmount?: number;
  discountPercentage?: number;
}

export type InvoiceUpdatePayload = Partial<InvoiceCreatePayload>;

export interface PaymentCreatePayload {
  amount: number;
  paymentDate: string;
  paymentMethod: string;
  reference?: string;
  notes?: string;
}

export type PaginatedInvoices = PaginatedResult<InvoiceSummary>;


export async function getInvoices(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
  customerId?: string;
  dateFrom?: string;
  dateTo?: string;
}): Promise<PaginatedInvoices> {
  const raw = await apiGet<PaginatedApiResponse<InvoiceSummary>>("invoices", params);
  return flattenPaginated(raw);
}

export function getInvoiceCounts(params?: { customerId?: string }) {
  return apiGet<InvoiceStatusCounts>("invoices/counts", params);
}

export function getInvoice(id: string) {
  return apiGet<InvoiceResponse>(`invoices/${id}`);
}

export function createInvoice(data: InvoiceCreatePayload) {
  return apiPost<InvoiceResponse>("invoices", data);
}

export function updateInvoice(
  id: string,
  data: InvoiceUpdatePayload,
  expectedVersion?: number
) {
  const path =
    expectedVersion != null
      ? `invoices/${id}?expected_version=${expectedVersion}`
      : `invoices/${id}`;
  return apiPut<InvoiceResponse>(path, data);
}

export function markAsSent(id: string, sentAt?: string) {
  return apiPost<InvoiceResponse>(
    `invoices/${id}/mark-sent`,
    sentAt ? { sentAt } : {}
  );
}

export function recordPayment(id: string, data: PaymentCreatePayload) {
  return apiPost<Payment>(`invoices/${id}/payments`, data);
}

export function duplicateInvoice(id: string) {
  return apiPost<InvoiceDuplicateResponse>(`invoices/${id}/duplicate`, {});
}

export function cancelInvoice(id: string) {
  return apiPost<InvoiceResponse>(`invoices/${id}/cancel`, {});
}

/** Download the invoice PDF (GET /invoices/{id}/pdf). Returns a Blob to save. */
export function downloadInvoicePdf(id: string) {
  return apiDownload(`invoices/${id}/pdf`);
}

export interface InvoiceSendPayload {
  toEmail?: string;
  subject?: string;
  body?: string;
  attachPdf?: boolean;
}

/** Send the invoice by email (POST /invoices/{id}/send). */
export function sendInvoice(id: string, data: InvoiceSendPayload = {}) {
  return apiPost<InvoiceSendResult>(`invoices/${id}/send`, data);
}