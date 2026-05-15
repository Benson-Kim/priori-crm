import type { PaginatedApiResponse } from "@/@types";
import { apiGet, apiPost, apiPut } from "./api";

// ─── Response Types (match backend schemas) ─────────────────────────

export interface InvoiceLineItem {
  id: string;
  line_number: number;
  description: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  tax_type: string;
  tax_amount: number;
  created_at: string;
  updated_at: string;
}

export interface Payment {
  id: string;
  amount: number;
  payment_date: string;
  payment_method: string;
  reference: string | null;
  notes: string | null;
  created_at: string;
  recorded_by: string | null;
}

export interface InvoiceSummary {
  id: string;
  invoice_number: string;
  invoice_reference: string;
  customer_id: string;
  customer_name: string;
  transaction_date: string;
  due_date: string;
  status: string;
  currency: string;
  total_due: number;
  balance_due: number;
  created_at: string;
  is_overdue: boolean;
  days_overdue: number;
}

export interface InvoiceResponse {
  id: string;
  invoice_number: string;
  invoice_reference: string;
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
  amount_paid: number;
  balance_due: number;
  rfq_number: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  paid_at: string | null;
  created_by: string | null;
  version: number;
  line_items: InvoiceLineItem[];
  payments: Payment[];
  is_editable: boolean;
  is_overdue: boolean;
  days_overdue: number;
}

export interface InvoiceStatusCounts {
  all: number;
  draft: number;
  sent: number;
  partial: number;
  paid: number;
  overdue: number;
  canceled: number;
}

export interface PaginatedInvoices {
  items: InvoiceSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

// ─── Request Payloads (camelCase aliases match backend) ─────────────

export interface LineItemPayload {
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface InvoiceCreatePayload {
  customerId: string;
  transactionDate: string;
  dueDate: string;
  currency: string;
  lineItems: LineItemPayload[];
  rfqNumber?: string;
  notes?: string;
  discountType?: string;
  discountAmount?: number;
  discountPercentage?: number;
}

export interface InvoiceUpdatePayload {
  customerId?: string;
  transactionDate?: string;
  dueDate?: string;
  currency?: string;
  lineItems?: LineItemPayload[];
  rfqNumber?: string;
  notes?: string;
  discountType?: string;
  discountAmount?: number;
  discountPercentage?: number;
}

export interface PaymentCreatePayload {
  amount: number;
  paymentDate: string;
  paymentMethod: string;
  reference?: string;
  notes?: string;
}

// ─── API Functions ──────────────────────────────────────────────────

export async function getInvoices(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
  customerId?: string;
  dateFrom?: string;
  dateTo?: string;
}): Promise<PaginatedInvoices> {
  const raw = await apiGet<PaginatedApiResponse<InvoiceSummary>>(
    "invoices",
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

export function getInvoiceCounts() {
  return apiGet<InvoiceStatusCounts>("invoices/counts");
}

export function getInvoice(id: string) {
  return apiGet<InvoiceResponse>(`invoices/${id}`);
}

export function createInvoice(data: InvoiceCreatePayload) {
  return apiPost<InvoiceResponse>("invoices", data);
}

export function updateInvoice(id: string, data: InvoiceUpdatePayload, expectedVersion?: number) {
  const path = expectedVersion != null
    ? `invoices/${id}?expected_version=${expectedVersion}`
    : `invoices/${id}`;
  return apiPut<InvoiceResponse>(path, data);
}

export function markAsSent(id: string, sentAt?: string) {
  return apiPost<InvoiceResponse>(`invoices/${id}/mark-sent`, sentAt ? { sentAt } : {});
}

export function recordPayment(id: string, data: PaymentCreatePayload) {
  return apiPost<Payment>(`invoices/${id}/payments`, data);
}

export function duplicateInvoice(id: string) {
  return apiPost<InvoiceResponse>(`invoices/${id}/duplicate`, {});
}

export function cancelInvoice(id: string) {
  return apiPost<InvoiceResponse>(`invoices/${id}/cancel`, {});
}
