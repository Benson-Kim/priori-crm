/**
 * Utility functions shared across Invoice and Quote document components.
 */

import { TAX_RATES } from "@/lib/constants";

export interface LineItemRow {
  key: string;
  itemName: string;
  description: string;
  quantity: string;
  unitPrice: string;
  taxType: string;
}

export interface DocumentTotals {
  subtotal: number;
  taxTotal: number;
  discountAmount: number;
  totalDue: number;
}

/**
 * Create a blank line item row with a unique key.
 */
export function createEmptyRow(): LineItemRow {
  return {
    key: generateRowKey(),
    itemName: "",
    description: "",
    quantity: "1",
    unitPrice: "0",
    taxType: "no_tax",
  };
}

/**
 * Generate a unique key for a line item row.
 * Falls back to Math.random for browsers without crypto.randomUUID.
 */
export function generateRowKey(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `row-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

/**
 * Calculate line total: quantity × unit price.
 */
export function calcLineTotal(qty: string, price: string): number {
  const q = parseFloat(qty) || 0;
  const p = parseFloat(price) || 0;
  return parseFloat((q * p).toFixed(2));
}

/**
 * Calculate tax amount for a line item.
 */
export function calcTaxAmount(lineTotal: number, taxType: string): number {
  const rate = TAX_RATES[taxType] ?? 0;
  return parseFloat((lineTotal * rate).toFixed(2));
}

/**
 * Calculate all invoice/quote totals from line items and discount.
 */
export function calculateTotals(
  lineItems: LineItemRow[],
  discountType: "amount" | "percentage" | null,
  discountValue: string
): DocumentTotals {
  let subtotal = 0;
  let taxTotal = 0;

  for (const item of lineItems) {
    const lineTotal = calcLineTotal(item.quantity, item.unitPrice);
    subtotal += lineTotal;
    taxTotal += calcTaxAmount(lineTotal, item.taxType);
  }

  subtotal = parseFloat(subtotal.toFixed(2));
  taxTotal = parseFloat(taxTotal.toFixed(2));

  const discountValueNum = parseFloat(discountValue) || 0;
  let discountAmount = 0;

  if (discountType === "amount") {
    discountAmount = Math.min(discountValueNum, subtotal);
  } else if (discountType === "percentage") {
    discountAmount = parseFloat(
      (subtotal * (discountValueNum / 100)).toFixed(2)
    );
  }

  const totalDue = parseFloat(
    (subtotal - discountAmount + taxTotal).toFixed(2)
  );

  return { subtotal, taxTotal, discountAmount, totalDue };
}

/**
 * Derive VAT category and rate from a taxType string.
 */
export function parseTaxType(taxType: string): {
  category: string;
  rate: string;
  isVat: boolean;
} {
  const isVat = taxType.startsWith("vat_");
  const rate = isVat ? (taxType.split("_")[1] ?? "0") : "0";
  const category = isVat ? "vat" : taxType === "exempt" ? "exempt" : "no_tax";
  return { category, rate, isVat };
}

/**
 * Derive taxType string from category and rate.
 */
export function buildTaxType(category: string, rate: string): string {
  if (category === "no_tax" || category === "exempt") return category;
  return `vat_${rate}`;
}

/**
 * Validate document form fields.
 * Returns a map of field -> error message.
 */
export function validateDocument(
  entityId: string,
  transactionDate: string,
  dueDate: string,
  lineItems: LineItemRow[],
  requireItemName = false,
  entityType = "Customer"
): Record<string, string> {
  const errors: Record<string, string> = {};

  if (!entityId) {
    errors[entityType.toLowerCase()] = `${entityType} is required`;
  }
  if (!transactionDate) {
    errors.transactionDate = "Transaction date is required";
  }
  if (!dueDate) {
    errors.dueDate = "Due date is required";
  }
  if (dueDate && transactionDate && dueDate < transactionDate) {
    errors.dueDate = "Due date must be on or after transaction date";
  }

  const validItems = lineItems.filter(
    (r) => r.description.trim() || r.itemName.trim()
  );

  if (validItems.length === 0) {
    errors.lineItems = "At least one line item is required";
  }

  for (const item of validItems) {
    const qty = parseFloat(item.quantity);
    const price = parseFloat(item.unitPrice);

    if (requireItemName && !item.itemName.trim()) {
      errors[`item_${item.key}_name`] = "Item name is required";
    }
    if (isNaN(qty) || qty <= 0) {
      errors[`item_${item.key}_qty`] = "Quantity must be greater than 0";
    }
    if (isNaN(price) || price < 0) {
      errors[`item_${item.key}_price`] = "Price must be 0 or greater";
    }
  }

  return errors;
}

/**
 * Build VAT label from tax types
 */
export function buildVatLabel(taxTypes: (string | undefined)[]): string {
  const vatTypes = Array.from(
    new Set(taxTypes.filter((t): t is string => !!t && t.startsWith("vat_")))
  );
  if (vatTypes.length === 0) return "VAT";
  if (vatTypes.length === 1) {
    const rate = vatTypes[0].split("_")[1];
    return rate ? `VAT (${rate}%)` : "VAT";
  }
  return "VAT (Mixed)";
}
