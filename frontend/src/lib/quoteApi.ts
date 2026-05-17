import type { PaginatedApiResponse } from "@/@types";
import { apiGet, apiPost, apiPut, apiDelete } from "./api";

// ─── Response Types (match backend schemas) ─────────────────────────

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
  customer: {
    id: string;
    display_name: string;
    email: string;
    phone: string;
    address?: string;
  };
  line_items: QuoteLineItem[];
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

export interface PaginatedQuotes {
  items: QuoteSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

// ─── Request Payloads (camelCase aliases match backend) ─────────────

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
  rfqRfpNumber?: string | null;
  notes?: string | null;
  discountType?: string | null;
  discountAmount?: number | null;
  discountPercentage?: number | null;
}

export interface QuoteUpdatePayload {
  customerId?: string;
  transactionDate?: string;
  dueDate?: string;
  currency?: string;
  lineItems?: QuoteLineItemPayload[];
  rfqRfpNumber?: string | null;
  notes?: string | null;
  discountType?: string | null;
  discountAmount?: number | null;
  discountPercentage?: number | null;
}

// ─── API Functions ──────────────────────────────────────────────────

export async function getQuotes(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
  customerId?: string;
  dateFrom?: string;
  dateTo?: string;
}): Promise<PaginatedQuotes> {
  const raw = await apiGet<PaginatedApiResponse<QuoteSummary>>(
    "quotes",
    params as Record<string, string | number | undefined>,
  );
  return {
    items: raw.items,
    total: raw.metadata.total,
    page: raw.metadata.page,
    per_page: raw.metadata.per_page,
    total_pages: raw.metadata.total_pages,
  };
}

export function getQuoteCounts() {
  return apiGet<QuoteStatusCounts>("quotes/counts");
}

export function getQuote(id: string) {
  return apiGet<QuoteResponse>(`quotes/${id}`);
}

export function getQuoteByNumber(quoteNumber: string) {
  return apiGet<QuoteResponse>(`quotes/number/${quoteNumber}`);
}

export function createQuote(data: QuoteCreatePayload) {
  return apiPost<QuoteResponse>("quotes", data);
}

export function updateQuote(id: string, data: QuoteUpdatePayload, expectedVersion?: number) {
  const path = expectedVersion != null
    ? `quotes/${id}?expected_version=${expectedVersion}`
    : `quotes/${id}`;
  return apiPut<QuoteResponse>(path, data);
}

export function markQuoteAsSent(id: string, sentAt?: string) {
  return apiPost<QuoteResponse>(`quotes/${id}/mark-sent`, sentAt ? { sentAt } : {});
}

export function approveQuote(id: string, notes?: string, approvedAt?: string) {
  return apiPost<QuoteResponse>(`quotes/${id}/approve`, { notes, approvedAt });
}

export function convertQuoteToInvoice(id: string) {
  return apiPost<{ quote_id: string; invoice_id: string; invoice_number: string }>(`quotes/${id}/convert-to-invoice`, {});
}

export function duplicateQuote(id: string) {
  return apiPost<{ original_quote_id: string; new_quote_id: string; new_quote_number: string }>(`quotes/${id}/duplicate`, {});
}

export function deleteQuote(id: string) {
  return apiDelete<void>(`quotes/${id}`);
}
