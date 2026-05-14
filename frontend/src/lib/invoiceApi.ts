import { apiGet, apiPost, apiPut } from "./api";

export interface Invoice {
  id: string;
  customer_type: string;
  company_name: string | null;
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  website: string | null;
  vat_number: string | null;
  currency: string;
  address: string;
  address2: string | null;
  country: string;
  province: string;
  city: string;
  postal_code: string;
  status: string;
  amount: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface PaginatedInvoice {
  items: Invoice[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface StatusCounts {
  all: number;
  active: number;
  inactive: number;
}

export interface InvoiceDetail {
  invoice: Invoice;
  invoices: {
    id: string;
    invoice_number: string;
    amount: number;
    status: string;
    date: string;
  }[];
  statements: {
    id: string;
    period: string;
    total_due: number;
    status: string;
  }[];
}

export function createInvoice(data: Record<string, unknown>) {
  return apiPost<Invoice>("invoices", data);
}

export function getInvoices(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
}) {
  return apiGet<PaginatedInvoice>("invoices", params);
}

export function getInvoice(id: string) {
  return apiGet<InvoiceDetail>(`invoice/${id}`);
}

export function updateInvoice(id: string, data: Record<string, unknown>) {
  return apiPut<Invoice>(`invoices/${id}`, data);
}

export function getInvoiceCounts() {
  return apiGet<StatusCounts>("invoices/counts");
}
