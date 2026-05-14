import type { PaginatedApiResponse } from "@/@types";
import { apiDelete, apiGet, apiPost, apiPut } from "./api";

export interface Customer {
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
  balance: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface CustomerSummary {
  id: string;
  customer_type: string;
  status: string;
  display_name: string;
  email: string;
  phone: string;
  balance: number;
  currency: string;
  created_at: string;
}



export interface PaginatedCustomers {
  items: CustomerSummary[];
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

export interface CustomerDetail {
  customer: Customer;
  financial_summary: {
    total_unpaid: number;
    overdue: number;
    total_invoiced: number;
    total_paid: number;
  };
  invoices: { id: string; invoice_number: string; amount: number; balance: number; status: string; date: string }[];
  statements: { id: string; period: string; opening_balance: number; invoiced_amount: number; amount_paid: number; balance_due: number }[];
}


export function createCustomer(data: Record<string, unknown>) {
  return apiPost<Customer>("customers", data);
}

export async function getCustomers(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
}): Promise<PaginatedCustomers> {
  const raw = await apiGet<PaginatedApiResponse<CustomerSummary>>(
    "customers",
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

export function getCustomer(id: string) {
  return apiGet<CustomerDetail>(`customers/${id}`);
}

export function updateCustomer(id: string, data: Record<string, unknown>) {
  return apiPut<Customer>(`customers/${id}`, data);
}

export function getCustomerCounts() {
  return apiGet<StatusCounts>("customers/counts");
}

export function activateCustomer(id: string) {
  return apiPost<Customer>(`customers/${id}/activate`, {});
}

export function deactivateCustomer(id: string, force = false) {
  return apiPost<Customer>(`customers/${id}/deactivate?force=${force}`, {});
}

export interface DeleteCheckResult {
  can_delete: boolean;
  can_hard_delete: boolean;
  delete_type: string;
  warnings: string[];
  associated_records: Record<string, number>;
  message: string;
}

export interface DeleteResult {
  customer_id: string;
  delete_type: string;
  deleted_at: string;
  warnings: string[];
  associated_records: Record<string, number>;
}

export function checkDeleteEligibility(id: string) {
  return apiGet<DeleteCheckResult>(`customers/${id}/delete-check`);
}

export function deleteCustomer(id: string, hardDelete = false, force = false) {
  return apiDelete<DeleteResult>(`customers/${id}?hard_delete=${hardDelete}&force=${force}`);
}
