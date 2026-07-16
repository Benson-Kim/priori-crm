import { apiDelete, apiDownload, apiGet, apiPost, apiPut, flattenPaginated } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import { buildStatementParams } from "@/lib/dateUtils";
import type { CustomerStatement, PaginatedApiResponse } from "@/lib/types";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type Customer = Schema<"CustomerResponse">;
export type CustomerSummary = Schema<"CustomerSummary">;
export type StatusCounts = Schema<"CustomerStatusCounts">;
export type DeleteCheckResult = Schema<"CustomerDeleteCheckResponse">;
export type DeleteResult = Schema<"CustomerDeleteResponse">;

export interface PaginatedCustomers {
  items: CustomerSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

/**
 * Row shape of CustomerDetailResponse.recent_invoices.
 *
 * The backend types this field as an untyped `list[dict]`, so the generated
 * contract cannot describe it. This hand-written interface documents the shape
 * the service actually emits and the detail page reads; tightening the backend
 * schema to a typed model is tracked as follow-up.
 */
export interface CustomerDetailInvoice {
  id: string;
  invoice_number: string;
  amount: number;
  balance: number;
  status: string;
  date: string;
}

export interface CustomerDetail {
  customer: Customer;
  financial_summary: Schema<"FinancialSummary">;
  recent_invoices: CustomerDetailInvoice[];
  statement: CustomerStatement | null;
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
  const raw = await apiGet<PaginatedApiResponse<CustomerSummary>>("customers", params);
  return flattenPaginated(raw);
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

export function checkDeleteEligibility(id: string) {
  return apiGet<DeleteCheckResult>(`customers/${id}/delete-check`);
}

export function deleteCustomer(id: string, hardDelete = false, force = false) {
  return apiDelete<DeleteResult>(`customers/${id}?hard_delete=${hardDelete}&force=${force}`);
}

export async function getCustomerStatement(
  customerId: string, periodStart?: string, periodEnd?: string
): Promise<CustomerStatement> {
  return apiGet<CustomerStatement>(
    `customers/${customerId}/statement`,
    buildStatementParams(periodStart, periodEnd)
  );
}

export function downloadCustomerStatementPdf(
  customerId: string, periodStart?: string, periodEnd?: string
): Promise<Blob> {
  return apiDownload(
    `customers/${customerId}/statement/pdf`,
    buildStatementParams(periodStart, periodEnd)
  );
}

export function exportCustomerStatementExcel(
  customerId: string, periodStart?: string, periodEnd?: string
): Promise<Blob> {
  return apiDownload(
    `customers/${customerId}/statement/excel`,
    buildStatementParams(periodStart, periodEnd)
  );
}
