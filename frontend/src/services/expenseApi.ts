import {
  apiDelete,
  apiDownload,
  apiGet,
  apiPost,
  apiPut,
  apiUpload,
  flattenPaginated,
} from "@/lib/api";
import type { PaginatedApiResponse } from "@/lib/types";

// Base models based on API schemas

export interface VendorSummary {
  id: string;
  vendor_name: string;
  email: string | null;
  phone_primary: string | null;
  phone_secondary: string | null;
}

export interface ExpenseLineItem {
  id?: string;
  item_name: string;
  description: string;
  quantity: number;
  unit_price: number;
  tax_type: string;
  line_total?: number;
  tax_amount?: number;
}

export interface ExpenseDocument {
  id: string;
  expense_id: string;
  filename: string;
  file_size_bytes: number;
  file_size_kb: number;
  mime_type: string;
  source: string;
  uploaded_at: string;
  uploaded_by: string | null;
}

export interface ExpensePayment {
  id: string;
  expense_id: string;
  amount: number;
  payment_date: string;
  reference: string | null;
  notes: string | null;
  document_id: string | null;
  created_at: string;
}

export interface ExpenseResponse {
  id: string;
  expense_number: string;
  expense_reference: string;
  vendor_id: string;
  expense_date: string;
  due_date: string;
  status: string;
  currency: string;
  is_recurring: boolean;
  subtotal: number;
  tax_total: number;
  total_due: number;
  amount_paid: number;
  balance_due: number;
  notes: string | null;
  created_at: string;
  updated_at: string;
  paid_at: string | null;
  version: number;
  vendor: VendorSummary;
  line_items: ExpenseLineItem[];
  payments: ExpensePayment[];
  documents: ExpenseDocument[];
  is_editable: boolean;
  is_overdue: boolean;
  days_overdue: number;
}

export interface ExpenseSummary {
  id: string;
  expense_number: string;
  expense_reference: string;
  vendor_id: string;
  vendor_name: string;
  expense_date: string;
  due_date: string;
  status: string;
  currency: string;
  total_due: number;
  balance_due: number;
  is_recurring: boolean;
  created_at: string;
  is_overdue: boolean;
  days_overdue: number;
}

export interface PaginatedExpenses {
  items: ExpenseSummary[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface ExpenseStatusCounts {
  all: number;
  pending: number;
  paid: number;
  overdue: number;
}

export interface ExpenseStatistics {
  total_expenses: number;
  total_amount: number;
  total_paid: number;
  total_outstanding: number;
  overdue_count: number;
  overdue_amount: number;
  average_expense_value: number;
  average_days_to_payment: number;
  date_from: string | null;
  date_to: string | null;
}

// Payloads

export interface ExpenseCreatePayload {
  vendorId: string;
  expenseDate: string;
  dueDate: string;
  currency: string;
  isRecurring: boolean;
  lineItems: ExpenseLineItem[];
  notes?: string | null;
}

export interface ExpenseUpdatePayload {
  vendorId?: string;
  expenseDate?: string;
  dueDate?: string;
  isRecurring?: boolean;
  lineItems?: ExpenseLineItem[];
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
  // The expenses router reads the version from the `expectedVersion` query
  // alias (P-9 / EXP-FE-4).
  const path =
    expectedVersion != null
      ? `expenses/${id}?expectedVersion=${expectedVersion}`
      : `expenses/${id}`;
  return apiPut<ExpenseResponse>(path, data);
}

export function deleteExpense(id: string) {
  return apiDelete<{ message: string }>(`expenses/${id}`);
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
  // Route through the shared client so the upload carries the bearer token
  // and the 401 -> refresh -> retry flow (LIB-FE-4 / EXP-FE-2).
  return apiUpload<ExpenseDocument>(`expenses/${id}/documents`, formData);
}

export function deleteExpenseDocument(id: string, documentId: string) {
  return apiDelete<{ message: string }>(
    `expenses/${id}/documents/${documentId}`
  );
}

export function downloadExpenseDocument(
  id: string,
  documentId: string
): Promise<Blob> {
  // Shared client adds auth + 401-refresh; returns a Blob the caller saves
  // via saveBlob (EXP-FE-1).
  return apiDownload(`expenses/${id}/documents/${documentId}/download`);
}

export function calculateTotals(data: any) {
  return apiPost<any>("expenses/calculate", data);
}
