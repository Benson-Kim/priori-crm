/**
 * Quote API service.
 */

import { apiDelete, apiGet, apiPost, apiPut, flattenPaginated } from "@/lib/api";
import type { PaginatedApiResponse, PaginatedResult } from "@/lib/types";


export interface QuoteLineItem {
  id: string;
  line_number: number;
  item_name: string;
  description: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  tax_type: string;
  tax_amount: number;
  created_at: string;
  updated_at: string;
}

export interface QuoteCustomer {
  id: string;
  display_name: string;
  email: string;
  phone: string;
  address?: string;
}

export interface QuoteSummary {
  id: string;
  quote_number: string;
  quote_reference: string;
  customer_id: string;
  customer_name: string;
  transaction_date: string;
  due_date: string;
  status: string;
  currency: string;
  total_due: number;
  created_at: string;
  is_expired: boolean;
  days_until_expiry: number;
}

export interface QuoteResponse {
  id: string;
  quote_number: string;
  quote_reference: string;
  customer_id: string;
  transaction_date: string;
  due_date: string;
  status: string;
  currency: string;
  subtotal: number;
  discount_type: string | null;
  discount_amount: number | null;
  discount_percentage: number | null;
  tax_total: number;
  total_due: number;
  rfq_rfp_number: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  approved_at: string | null;
  invoiced_at: string | null;
  expired_at: string | null;
  created_by: string | null;
  approved_by: string | null;
  version: number;
  related_invoice_id: string | null;
  line_items: QuoteLineItem[];
  customer: QuoteCustomer;
  is_editable: boolean;
  is_expired: boolean;
  days_until_expiry: number;
  can_convert_to_invoice: boolean;
}

export interface QuoteStatusCounts {
  all: number;
  draft: number;
  sent: number;
  approved: number;
  invoiced: number;
  expired: number;
}

export interface QuoteConvertResponse {
  quote_id: string;
  invoice_id: string;
  invoice_number: string;
  message: string;
}


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
  return apiPost<QuoteResponse>(`quotes/${id}/duplicate`, {});
}

export function deleteQuote(id: string) {
  return apiDelete<void>(`quotes/${id}`);
}