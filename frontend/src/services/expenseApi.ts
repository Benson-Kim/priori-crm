/**
 * Expense API service.
 *
 * Response types are sourced from the generated OpenAPI contract
 * (`@/lib/apiTypes`) rather than hand-mirrored Pydantic schemas.
 * Request *payloads* stay hand-written as the camelCase transport shape the
 * API maps onto its snake_case fields via Pydantic aliases.
 */

import {
  apiDelete,
  apiDownload,
  apiGet,
  apiPost,
  apiPut,
  apiUpload,
  flattenPaginated,
} from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import type { PaginatedApiResponse } from "@/lib/types";
import { createSearchParams } from "@/lib/utils";

// Response contracts (generated from the FastAPI OpenAPI schema).
export type ExpenseResponse = Schema<"ExpenseResponse">;
// Two VendorSummary schemas exist (vendors + expenses modules); the
// expense-flavoured one is only ever ExpenseResponse.vendor, so derive it
// from there to avoid the ambiguous bare Schema<"VendorSummary"> lookup.
export type VendorSummary = ExpenseResponse["vendor"];
export type ExpenseLineItem = Schema<"ExpenseLineItemResponse">;
export type ExpenseDocument = Schema<"ExpenseDocumentResponse">;
export type ExpensePayment = Schema<"ExpensePaymentResponse">;
export type ExpenseSummary = Schema<"ExpenseSummary">;
export type ExpenseStatusCounts = Schema<"ExpenseStatusCounts">;
export type ExpenseStatistics = Schema<"ExpenseStatisticsResponse">;
export type ExpenseCalculationResponse = Schema<"ExpenseCalculationResponse">;

// Payloads (hand-written camelCase transport shape).

export interface ExpenseLineItemPayload {
  itemName: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface PaginatedExpenses {
  items: ExpenseSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface ExpenseCreatePayload {
  vendorId: string;
  expenseDate: string;
  dueDate: string;
  currency: string;
  isRecurring: boolean;
  lineItems: ExpenseLineItemPayload[];
  notes?: string | null;
}

export interface ExpenseUpdatePayload {
  vendorId?: string;
  expenseDate?: string;
  dueDate?: string;
  isRecurring?: boolean;
  lineItems?: ExpenseLineItemPayload[];
  notes?: string | null;
}

export interface ExpensePaymentPayload {
  amount: number;
  paymentDate: string;
  reference?: string | null;
  notes?: string | null;
}

// API Functions

export async function getExpenses(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
}): Promise<PaginatedExpenses> {
  const raw = await apiGet<PaginatedApiResponse<ExpenseSummary>>(
    "expenses",
    params
  );
  return flattenPaginated(raw);
}

export function getExpense(id: string) {
  return apiGet<ExpenseResponse>(`expenses/${id}`);
}

export function createExpense(data: ExpenseCreatePayload) {
  return apiPost<ExpenseResponse>("expenses", data);
}

export function updateExpense(
  id: string,
  data: ExpenseUpdatePayload,
  expectedVersion?: number
) {
  const path =
    expectedVersion != null
      ? `expenses/${id}?expectedVersion=${expectedVersion}`
      : `expenses/${id}`;
  return apiPut<ExpenseResponse>(path, data);
}

export function deleteExpense(id: string) {
  return apiDelete<void>(`expenses/${id}`);
}

export function getExpenseCounts() {
  return apiGet<ExpenseStatusCounts>("expenses/counts");
}

export function getExpenseStatistics() {
  return apiGet<ExpenseStatistics>("expenses/stats/summary");
}

export function markAsPaid(id: string, data?: { paidAt?: string }) {
  return apiPost<ExpenseResponse>(`expenses/${id}/mark-paid`, data || {});
}

export function recordPayment(id: string, data: ExpensePaymentPayload) {
  return apiPost<ExpensePayment>(`expenses/${id}/payments`, data);
}

export async function uploadExpenseDocument(
  id: string,
  file: File
): Promise<ExpenseDocument> {
  const formData = new FormData();
  formData.append("file", file);
  return apiUpload<ExpenseDocument>(`expenses/${id}/documents`, formData);
}

export function deleteExpenseDocument(id: string, documentId: string) {
  return apiDelete<void>(
    `expenses/${id}/documents/${documentId}`
  );
}

export function downloadExpenseDocument(
  id: string,
  documentId: string
): Promise<Blob> {
  return apiDownload(`expenses/${id}/documents/${documentId}/download`);
}

/**
 * Preview expense totals. Posts the camelCase ExpenseLineItemPayload shape
 * (the documented POST /expenses/calculate contract), not snake_case keys
 * relying on the backend's populate_by_name fallback.
 */
export function calculateTotals(data: ExpenseLineItemPayload[]) {
  return apiPost<ExpenseCalculationResponse>("expenses/calculate", data);
}

/**
 * Download the currently-filtered expenses as an .xlsx workbook. Returns a
 * Blob the caller saves with `saveBlob`
 */
export function exportExpensesExcel(params?: {
  status?: string;
  vendorId?: string;
  dateFrom?: string;
  dateTo?: string;
  isRecurring?: boolean;
  includeLineItems?: boolean;
}): Promise<Blob> {
  const query = params ? createSearchParams(params) : "";
  const path = query ? `expenses/export/excel?${query}` : "expenses/export/excel";
  return apiDownload(path);
}
