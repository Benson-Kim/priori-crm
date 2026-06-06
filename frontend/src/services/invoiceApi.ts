/**
 * Invoice API service.
 */

import { apiDownload, apiGet, apiPost, apiPut, flattenPaginated } from "@/lib/api";
import type { PaginatedApiResponse, PaginatedResult } from "@/lib/types";


export interface InvoiceLineItem {
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

export interface InvoiceCustomer {
  id: string;
  display_name: string;
  email: string;
  phone: string;
  address?: string;
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
  customer: InvoiceCustomer;
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
  currency: string;
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

export interface InvoiceDuplicateResponse {
  original_invoice_id: string;
  new_invoice_id: string;
  new_invoice_number: string;
  message: string;
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

export interface InvoiceSendResult {
  invoice_id: string;
  sent_to: string;
  sent_at: string;
  message: string;
}

/** Send the invoice by email (POST /invoices/{id}/send). */
export function sendInvoice(id: string, data: InvoiceSendPayload = {}) {
  return apiPost<InvoiceSendResult>(`invoices/${id}/send`, data);
}