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
 * Round a monetary value to 2 decimal places using ROUND_HALF_UP.
 *
 * Single source of truth for money rounding on the frontend. Mirrors the
 * backend policy in api/app/common/financial.quantize_money so preview
 * totals always equal the persisted/PDF totals for the same input
 *. Uses an epsilon nudge to defend against binary-float
 * representation errors (e.g. 1.005) before half-up rounding.
 */
export function roundMoney(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

/**
 * Calculate line total: quantity × unit price (quantized to 2 dp).
 */
export function calcLineTotal(qty: string, price: string): number {
  const q = parseFloat(qty) || 0;
  const p = parseFloat(price) || 0;
  return roundMoney(q * p);
}

/**
 * Calculate tax amount for a line item (quantized to 2 dp).
 */
export function calcTaxAmount(lineTotal: number, taxType: string): number {
  const rate = TAX_RATES[taxType] ?? 0;
  return roundMoney(lineTotal * rate);
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

  // Line values are already quantized; re-round the sums to settle any
  // binary-float accumulation error.
  subtotal = roundMoney(subtotal);
  taxTotal = roundMoney(taxTotal);

  const discountValueNum = parseFloat(discountValue) || 0;
  let discountAmount = 0;

  if (discountType === "amount") {
    discountAmount = roundMoney(Math.min(discountValueNum, subtotal));
  } else if (discountType === "percentage") {
    discountAmount = roundMoney(subtotal * (discountValueNum / 100));
  }

  const totalDue = roundMoney(subtotal - discountAmount + taxTotal);

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
  const isPetroleumVat = taxType.startsWith("petroleum_vat_");
  const isVat = taxType.startsWith("vat_") || isPetroleumVat;
  const rate = isPetroleumVat
    ? (taxType.split("_")[2] ?? "0")
    : isVat
      ? (taxType.split("_")[1] ?? "0")
      : "0";
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

/*
 * PO-level VAT (PO-27).
 *
 * Purchase-order VAT is a single charge on the subtotal, not a per-line tax.
 * These helpers are the single frontend source of truth for the computation
 * and the label so the editor preview, the Overview viewer and (conceptually)
 * the PDF all agree. The computation mirrors the backend
 * common.financial.calculate_subtotal_vat exactly (roundMoney implements the
 * same ROUND_HALF_UP money policy as the backend quantize_money), so the
 * client preview equals the persisted/PDF total for the same input.
 */

/**
 * Compute PO-level VAT on the subtotal. Returns 0 when VAT is disabled or the
 * rate is missing/non-positive.
 *
 * @param subtotal Document subtotal.
 * @param enabled  Whether PO-level VAT is on.
 * @param rate     VAT rate as a fraction (e.g. 0.16 for 16%).
 */
export function calcSubtotalVat(
  subtotal: number,
  enabled: boolean,
  rate: number | null | undefined
): number {
  if (!enabled || rate == null || rate <= 0) return 0;
  return roundMoney(subtotal * rate);
}

/**
 * Build the VAT label from tax types.
 *
 * Mirrors the PDF: "VAT {rate}% ({ref})", with the percent trimmed of trailing
 * zeros (0.16 -> "16", 0.075 -> "7.5") and the compliance ref appended when
 * present. Falls back to a bare "VAT" when the rate is missing/non-positive.
 *
 * @param rate          VAT rate as a fraction (e.g. 0.16 for 16%).
 * @param complianceRef Optional VAT / compliance reference.
 */
export function buildVatLabel(
  rate: number | null | undefined,
  complianceRef?: string | null
): string {
  if (rate == null || rate <= 0) return "VAT";
  // Fraction -> percent, trimming trailing zeros via Number round-trip.
  const pct = Number((rate * 100).toFixed(4)).toString();
  const ref = complianceRef?.trim();
  return ref ? `VAT ${pct}% (${ref})` : `VAT ${pct}%`;
}
