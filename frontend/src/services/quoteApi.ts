/**
 * Quote API service.
 *
 * Response types are sourced from the generated OpenAPI contract
 * (`@/lib/apiTypes`) rather than hand-mirrored Pydantic schemas — the
 * hand-written copies were the root cause of the duplicate-response drift
 * (ISSUE-060) and similar (#33). Request *payloads* stay hand-written: they
 * are the camelCase transport shape the frontend sends, which the API maps
 * onto its snake_case fields via Pydantic aliases.
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
import type { PaginatedApiResponse, PaginatedResult } from "@/lib/types";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type QuoteLineItem = Schema<"QuoteLineItemResponse">;
export type QuoteCustomer = Schema<"CustomerSummary">;
export type QuoteSummary = Schema<"QuoteSummary">;
export type QuoteResponse = Schema<"QuoteResponse">;
export type QuoteStatusCounts = Schema<"QuoteStatusCounts">;
export type QuoteConvertResponse = Schema<"QuoteConvertToInvoiceResponse">;
export type QuoteDuplicateResponse = Schema<"QuoteDuplicateResponse">;
export type QuoteSendResult = Schema<"QuoteSendResponse">;

export interface QuoteLineItemPayload {
  itemName: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface QuoteCreatePayload {
  customerId: string;
  transactionDate: string;
  dueDate: string;
  currency: string;
  lineItems: QuoteLineItemPayload[];
  rfqRfpNumber?: string;
  notes?: string;
  discountType?: string;
  discountAmount?: number;
  discountPercentage?: number;
}

export type QuoteUpdatePayload = Partial<QuoteCreatePayload>;

export type PaginatedQuotes = PaginatedResult<QuoteSummary>;


export async function getQuotes(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
  customerId?: string;
  dateFrom?: string;
  dateTo?: string;
}): Promise<PaginatedQuotes> {
  const raw = await apiGet<PaginatedApiResponse<QuoteSummary>>("quotes", params);
  return flattenPaginated(raw);
}

export function getQuoteCounts() {
  return apiGet<QuoteStatusCounts>("quotes/counts");
}

export function getQuote(id: string) {
  return apiGet<QuoteResponse>(`quotes/${id}`);
}

export function createQuote(data: QuoteCreatePayload) {
  return apiPost<QuoteResponse>("quotes", data);
}

export function updateQuote(
  id: string,
  data: QuoteUpdatePayload,
  expectedVersion?: number
) {
  const path =
    expectedVersion != null
      ? `quotes/${id}?expected_version=${expectedVersion}`
      : `quotes/${id}`;
  return apiPut<QuoteResponse>(path, data);
}

export function markQuoteAsSent(id: string, sentAt?: string) {
  return apiPost<QuoteResponse>(
    `quotes/${id}/mark-sent`,
    sentAt ? { sentAt } : {}
  );
}

export function approveQuote(id: string, approvedAt?: string) {
  return apiPost<QuoteResponse>(
    `quotes/${id}/approve`,
    approvedAt ? { approvedAt } : {}
  );
}

export function convertQuoteToInvoice(id: string) {
  return apiPost<QuoteConvertResponse>(`quotes/${id}/convert-to-invoice`, {});
}

export function duplicateQuote(id: string) {
  return apiPost<QuoteDuplicateResponse>(`quotes/${id}/duplicate`, {});
}

export function deleteQuote(id: string) {
  return apiDelete<void>(`quotes/${id}`);
}

export interface QuoteSendPayload {
  toEmail?: string;
  subject?: string;
  body?: string;
  attachPdf?: boolean;
}

/** Send the quote by email. */
export function sendQuote(id: string, data: QuoteSendPayload = {}) {
  return apiPost<QuoteSendResult>(`quotes/${id}/send`, data);
}

/** Download the quote PDF. Returns a Blob to save. */
export function downloadQuotePdf(id: string) {
  return apiDownload(`quotes/${id}/pdf`);
}