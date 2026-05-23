import { apiDelete, apiGet, apiPost, apiPut, flattenPaginated } from "@/lib/api";
import type { PaginatedApiResponse } from "@/lib/types";

// Note: VendorStatement interfaces are mirrored from CustomerStatement
export interface VendorStatement {
  vendor: {
    id: string;
    vendor_name: string;
    email: string;
    phone: string;
    currency: string;
  };
  period_start: string;
  period_end: string;
  summary: {
    opening_balance: number;
    invoiced_amount: number;
    amount_paid: number;
    balance_due: number;
  };
  transactions: {
    date: string;
    description: string;
    amount: number;
    payment: number;
    balance: number;
  }[];
}

export interface Vendor {
  id: string;
  vendor_name: string;
  address: string | null;
  email: string | null;
  country: string | null;
  phone_primary: string | null;
  phone_secondary: string | null;
  website: string | null;
  currency: string;
  vat_number: string | null;
  tax_id_pin: string | null;
  notes: string | null;
  status: string;
  contact_id: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  vendor_since_year: number;
  display_initials: string;
  payables: number;
  total_unpaid: number;
  overdue_total: number;
}

export interface VendorSummary {
  id: string;
  vendor_name: string;
  email: string | null;
  phone_primary: string | null;
  status: string;
  currency: string;
  payables: number;
  created_at: string;
  is_active: boolean;
}

export interface PaginatedVendors {
  items: VendorSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface VendorStatusCounts {
  all: number;
  active: number;
  inactive: number;
}

export interface VendorPayablesSummary {
  total_unpaid: number;
  overdue_total: number;
  currency: string;
}

export interface VendorDetailResponse {
  vendor: Vendor;
  payables_summary: VendorPayablesSummary;
  transaction_count: number;
  next_actions: string[];
}

export interface VendorTransactionSummary {
  id: string;
  transaction_type: string;
  ref_no: string;
  date: string;
  amount: number;
  balance: number;
  status: string;
  due_date: string | null;
  days_overdue: number;
  status_display: string;
}

export interface PaginatedVendorTransactions {
  items: VendorTransactionSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface DeleteResult {
  vendor_id: string;
  message: string;
}

export interface DuplicateCheckResult {
  exists: boolean;
  vendor: VendorSummary | null;
  message: string | null;
}

export interface ContactSearchResult {
  id: string;
  full_name: string;
  email: string | null;
  phone_primary: string | null;
  address: string | null;
  country: string | null;
  website: string | null;
  vat_number: string | null;
}

export interface ContactSearchResponse {
  results: ContactSearchResult[];
  total: number;
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
