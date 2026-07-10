
import type { components } from "./api-schema";

export type UserRole = components["schemas"]["UserRole"];
export type CustomerStatus = components["schemas"]["CustomerStatus"];
export type CustomerType = components["schemas"]["CustomerType"];
export type Currency = components["schemas"]["Currency"];
export type TransactionType = components["schemas"]["TransactionType"];
export type InvoiceStatus = components["schemas"]["InvoiceStatus"];
export type PaymentMethod = components["schemas"]["PaymentMethod"];
export type TaxType = components["schemas"]["TaxType"];
export type DiscountType = components["schemas"]["DiscountType"];
export type QuoteStatus = components["schemas"]["QuoteStatus"];
export type VendorStatus = components["schemas"]["VendorStatus"];
export type PayableTransactionStatus =
     components["schemas"]["PayableTransactionStatus"];
export type ExpenseStatus = components["schemas"]["ExpenseStatus"];
export type DocumentSource = components["schemas"]["DocumentSource"];
export type PurchaseOrderStatus =
     components["schemas"]["PurchaseOrderStatus"];

export const userRoleOptions = [
     { value: "admin", label: "Admin" },
     { value: "manager", label: "Manager" },
     { value: "member", label: "Member" },
] satisfies Array<{ value: UserRole; label: string }>;

export const customerStatusOptions = [
     { value: "active", label: "Active" },
     { value: "inactive", label: "Inactive" },
     { value: "suspended", label: "Suspended" },
     { value: "deleted", label: "Deleted" },
] satisfies Array<{ value: CustomerStatus; label: string }>;

export const customerTypeOptions = [
     { value: "individual", label: "Individual" },
     { value: "business", label: "Business" },
] satisfies Array<{ value: CustomerType; label: string }>;

export const currencyOptions = [
     { value: "KES", label: "KES" },
     { value: "USD", label: "USD" },
     { value: "EUR", label: "EUR" },
     { value: "GBP", label: "GBP" },
] satisfies Array<{ value: Currency; label: string }>;

export const transactionTypeOptions = [
     { value: "sale", label: "Sale" },
     { value: "refund", label: "Refund" },
     { value: "payment", label: "Payment" },
     { value: "adjustment", label: "Adjustment" },
] satisfies Array<{ value: TransactionType; label: string }>;

export const invoiceStatusOptions = [
     { value: "draft", label: "Draft" },
     { value: "sent", label: "Sent" },
     { value: "partial", label: "Partial" },
     { value: "paid", label: "Paid" },
     { value: "overdue", label: "Overdue" },
     { value: "canceled", label: "Canceled" },
] satisfies Array<{ value: InvoiceStatus; label: string }>;

export const paymentMethodOptions = [
     { value: "cash", label: "Cash" },
     { value: "bank_transfer", label: "Bank Transfer" },
     { value: "check", label: "Check" },
     { value: "card", label: "Card" },
     { value: "mobile_money", label: "Mobile Money" },
     { value: "other", label: "Other" },
] satisfies Array<{ value: PaymentMethod; label: string }>;

export const taxTypeOptions = [
     { value: "vat_16", label: "VAT 16%" },
     { value: "vat_8", label: "VAT 8%" },
     { value: "vat_0", label: "Zero-rated VAT" },
     { value: "exempt", label: "Exempt" },
     { value: "no_tax", label: "No Tax" },
] satisfies Array<{ value: TaxType; label: string }>;

export const discountTypeOptions = [
     { value: "amount", label: "Amount" },
     { value: "percentage", label: "Percentage" },
] satisfies Array<{ value: DiscountType; label: string }>;

export const quoteStatusOptions = [
     { value: "draft", label: "Draft" },
     { value: "sent", label: "Sent" },
     { value: "approved", label: "Approved" },
     { value: "invoiced", label: "Invoiced" },
     { value: "expired", label: "Expired" },
] satisfies Array<{ value: QuoteStatus; label: string }>;

export const vendorStatusOptions = [
     { value: "active", label: "Active" },
     { value: "inactive", label: "Inactive" },
] satisfies Array<{ value: VendorStatus; label: string }>;

export const payableTransactionStatusOptions = [
     { value: "paid", label: "Paid" },
     { value: "pending", label: "Pending" },
     { value: "overdue", label: "Overdue" },
] satisfies Array<{ value: PayableTransactionStatus; label: string }>;

export const expenseStatusOptions = [
     { value: "pending", label: "Pending" },
     { value: "paid", label: "Paid" },
     { value: "overdue", label: "Overdue" },
     { value: "canceled", label: "Canceled" },
] satisfies Array<{ value: ExpenseStatus; label: string }>;

export const documentSourceOptions = [
     { value: "form", label: "Form" },
     { value: "view", label: "View" },
     { value: "payment_modal", label: "Payment Modal" },
] satisfies Array<{ value: DocumentSource; label: string }>;

export const purchaseOrderStatusOptions = [
     { value: "draft", label: "Draft" },
     { value: "sent", label: "Sent" },
     { value: "paid", label: "Paid" },
] satisfies Array<{ value: PurchaseOrderStatus; label: string }>;