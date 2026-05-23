/**
 * DocumentEditor — unified editor for both Invoice and Quote documents.
 *
 * Single Responsibility: manages form state and delegates rendering
 * to sub-components (LineItemsTable, DocumentTotalsPanel, CustomerSelector).
 *
 * Open/Closed: extend via `type` prop — never duplicate this component.
 */

import { CustomerSelector } from "@/components/modals/CustomerSelector";
import { Button } from "@/components/ui/Button";
import {
  COMPANY_INFO,
  CURRENCY_OPTIONS,
  DEFAULT_DUE_DATE_DAYS,
} from "@/lib/constants";
import { formatDisplayDate } from "@/lib/utils";
import { Save, SquarePen } from "lucide-react";
import { useMemo, useState } from "react";
import { Divider } from "../ui/Divider";
import { DocumentTotalsPanel } from "./layout/document-totals";
import { LineItemsTable } from "./layout/line-items-table";
import {
  calculateTotals,
  createEmptyRow,
  validateDocument,
  buildVatLabel,
  type LineItemRow,
} from "./utils";

import { getTodayString, getDefaultDueDate } from "@/lib/dateUtils";

// Types

export interface DocumentLineItemPayload {
  itemName: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface DocumentPayload {
  customerId: string;
  transactionDate: string;
  dueDate: string;
  currency: string;
  rfqNumber?: string;
  notes?: string;
  discountType?: "amount" | "percentage";
  discountAmount?: number;
  discountPercentage?: number;
  lineItems: DocumentLineItemPayload[];
}

export interface DocumentInitialData {
  documentReference?: string;
  customerId?: string;
  customer?: {
    display_name: string;
    email: string;
    phone: string;
    address?: string;
  };
  transactionDate?: string;
  dueDate?: string;
  currency?: string;
  rfqNumber?: string;
  notes?: string;
  discountType?: "amount" | "percentage" | null;
  discountAmount?: number | null;
  discountPercentage?: number | null;
  lineItems?: {
    id: string;
    itemName?: string;
    description: string;
    quantity: number;
    unitPrice: number;
    taxType?: string;
  }[];
}

interface DocumentEditorProps {
  type: "invoice" | "quote";
  initialData?: DocumentInitialData;
  onSave: (payload: DocumentPayload) => Promise<void>;
  onPreview?: () => void;
  isLoading: boolean;
  restrictedMode?: boolean;
}

// Component

export function DocumentEditor({
  type,
  initialData,
  onSave,
  onPreview,
  isLoading,
  restrictedMode = false,
}: Readonly<DocumentEditorProps>) {
  const label = type === "invoice" ? "INVOICE" : "QUOTE";

  // State
  const [customerId, setCustomerId] = useState(initialData?.customerId ?? "");

  const [transactionDate, setTransactionDate] = useState<string>(() => {
    const now = Date.now();
    return initialData?.transactionDate ?? getTodayString(now);
  });

  const [dueDate, setDueDate] = useState<string>(() => {
    const now = Date.now();
    return (
      initialData?.dueDate ?? getDefaultDueDate(DEFAULT_DUE_DATE_DAYS, now)
    );
  });

  const [currency, setCurrency] = useState(initialData?.currency ?? "KES");
  const [rfqNumber, setRfqNumber] = useState(initialData?.rfqNumber ?? "");
  const [notes, setNotes] = useState(initialData?.notes ?? "");

  const [discountType, setDiscountType] = useState<
    "amount" | "percentage" | null
  >(initialData?.discountType ?? null);
  const [discountValue, setDiscountValue] = useState<string>(
    initialData?.discountType === "amount"
      ? String(initialData?.discountAmount ?? "")
      : initialData?.discountType === "percentage"
        ? String(initialData?.discountPercentage ?? "")
        : ""
  );

  const [lineItems, setLineItems] = useState<LineItemRow[]>(() => {
    if (initialData?.lineItems?.length) {
      return initialData.lineItems.map((li) => ({
        key: li.id,
        itemName: li.itemName ?? li.description.split("\n")[0] ?? "",
        description: li.description,
        quantity: String(li.quantity),
        unitPrice: String(li.unitPrice),
        taxType: li.taxType ?? "no_tax",
      }));
    }
    return [createEmptyRow()];
  });

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Derived totals (memoized)
  const totals = useMemo(
    () => calculateTotals(lineItems, discountType, discountValue),
    [lineItems, discountType, discountValue]
  );

  const vatLabel = useMemo(
    () => buildVatLabel(lineItems.map((item) => item.taxType)),
    [lineItems]
  );

  // Line item handlers
  const addRow = () => setLineItems((prev) => [...prev, createEmptyRow()]);

  const removeRow = (key: string) =>
    setLineItems((prev) => prev.filter((r) => r.key !== key));

  const updateRow = (key: string, field: keyof LineItemRow, value: string) =>
    setLineItems((prev) =>
      prev.map((r) => (r.key === key ? { ...r, [field]: value } : r))
    );

  // Submit
  const handleSubmit = async () => {
    setSubmitError(null);

    const validationErrors = validateDocument(
      customerId,
      transactionDate,
      dueDate,
      lineItems,
      type === "quote" // quotes require itemName
    );

    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) return;

    const validItems = lineItems.filter(
      (r) => r.description.trim() || r.itemName.trim()
    );

    const items: DocumentLineItemPayload[] = validItems.map((r) => ({
      itemName: r.itemName.trim(),
      description: r.description.trim(),
      quantity: Number.parseFloat(r.quantity),
      unitPrice: Number.parseFloat(r.unitPrice),
      taxType: r.taxType || "no_tax",
    }));

    const payload: DocumentPayload = {
      customerId,
      transactionDate,
      dueDate,
      currency,
      lineItems: items,
      rfqNumber: rfqNumber.trim() || undefined,
      notes: notes.trim() || undefined,
      discountType: discountType ?? undefined,
      discountAmount:
        discountType === "amount"
          ? Number.parseFloat(discountValue) || undefined
          : undefined,
      discountPercentage:
        discountType === "percentage"
          ? Number.parseFloat(discountValue) || undefined
          : undefined,
    };

    try {
      await onSave(payload);
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to save. Please try again."
      );
    }
  };

  return (
    <div className="flex flex-col gap-6 font-sans">
      <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden shadow-sm">
        {/* Top Section */}
        <div className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {/* Logo */}
            <div className="flex flex-col items-start gap-3">
              <img
                src="/Logo Priori.svg"
                alt="Priori logo"
                className="aspect-150/44 w-37.5"
              />
              <button
                type="button"
                className="text-priori-purple font-bold text-sm flex items-center gap-1 hover:underline"
              >
                Update <SquarePen size={20} />
              </button>
            </div>

            {/* Company Info */}
            <div className="flex flex-col gap-1 text-gray-800">
              <h3 className="font-bold text-base mb-1">{COMPANY_INFO.name}</h3>
              <p className="text-sm">{COMPANY_INFO.address}</p>
              <p className="text-sm">{COMPANY_INFO.phone}</p>
              <p className="text-sm mb-1">{COMPANY_INFO.email}</p>
              <button
                type="button"
                className="text-priori-purple font-bold text-sm flex items-center gap-1 hover:underline w-fit"
              >
                Update <SquarePen size={20} />
              </button>
            </div>

            {/* Document To (Customer Selector) */}
            <div className="flex flex-col gap-1">
              <h2 className="text-[22px] font-black text-gray-800 tracking-wider mb-1 uppercase">
                {label}
              </h2>
              <CustomerSelector
                label="To"
                initialCustomerId={initialData?.customerId}
                initialCustomerName={initialData?.customer?.display_name}
                initialCustomerDetails={
                  initialData?.customer
                    ? {
                        address: initialData.customer.address,
                        phone: initialData.customer.phone,
                        email: initialData.customer.email,
                      }
                    : null
                }
                onChange={setCustomerId}
                restrictedMode={restrictedMode}
                error={errors.customer}
              />
            </div>
          </div>
        </div>

        {/* Metadata Row */}
        <div className="p-6">
          <div className="grid grid-cols-2 md:grid-cols-10 gap-6">
            {/* Reference */}
            <div className="flex flex-col gap-2 md:col-span-2">
              <span className="text-gray-500 font-medium">Reference</span>
              <p className="font-bold text-gray-800">
                {initialData?.documentReference ?? "Autogenerated"}
              </p>
            </div>

            {/* Transaction Date */}
            <DateField
              label="Transaction Date"
              value={transactionDate}
              onChange={setTransactionDate}
              error={errors.transactionDate}
              restrictedMode={restrictedMode}
              colSpan="md:col-span-2"
            />

            {/* Due Date */}
            <DateField
              label="Due Date"
              value={dueDate}
              onChange={setDueDate}
              error={errors.dueDate}
              restrictedMode={restrictedMode}
              colSpan="md:col-span-2"
            />

            {/* RFQ/RFP Number */}
            <div className="flex flex-col gap-2 min-w-0 md:col-span-3">
              <span className="text-gray-500 font-medium">RFQ/RFP Number</span>
              <label
                htmlFor="rfq-input"
                className="flex items-center gap-2 w-fit max-w-full cursor-text"
              >
                <div className="relative flex items-center min-w-0">
                  <span className="invisible font-bold truncate block">
                    {rfqNumber || "Enter"}
                  </span>
                  <input
                    id="rfq-input"
                    type="text"
                    value={rfqNumber}
                    onChange={(e) => setRfqNumber(e.target.value)}
                    placeholder="Enter"
                    disabled={restrictedMode}
                    className="font-bold text-priori-purple outline-none bg-transparent w-full h-full placeholder-priori-purple/70 absolute inset-0 truncate"
                  />
                </div>
                {!restrictedMode && (
                  <SquarePen
                    size={20}
                    className="text-priori-purple shrink-0 cursor-pointer"
                  />
                )}
              </label>
            </div>

            {/* Currency */}
            <div className="flex flex-col gap-2 md:col-span-1">
              <span className="text-gray-500 font-medium">Currency</span>
              <div className="flex items-center gap-2 relative w-fit min-w-20">
                <span className="font-bold text-priori-purple whitespace-nowrap">
                  {currency}
                </span>
                {!restrictedMode && (
                  <>
                    <SquarePen
                      size={20}
                      className="text-priori-purple pointer-events-none shrink-0"
                    />
                    <select
                      value={currency}
                      onChange={(e) => setCurrency(e.target.value)}
                      className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                    >
                      {CURRENCY_OPTIONS.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>

        <Divider />

        {/* Line Items */}
        <div className="p-6 flex flex-col gap-6">
          <h3 className="text-[20px] leading-7 font-bold text-gray-800">
            Item Details
          </h3>
          {errors.lineItems && (
            <p className="text-xs text-red-500">{errors.lineItems}</p>
          )}

          <LineItemsTable
            lineItems={lineItems}
            errors={errors}
            restrictedMode={restrictedMode}
            onAddRow={addRow}
            onRemoveRow={removeRow}
            onUpdateRow={updateRow}
          />

          {/* Totals */}
          <DocumentTotalsPanel
            totals={totals}
            currency={currency}
            discountType={discountType}
            discountValue={discountValue}
            vatLabel={vatLabel}
            restrictedMode={restrictedMode}
            onDiscountTypeChange={setDiscountType}
            onDiscountValueChange={setDiscountValue}
            onDiscountRemove={() => {
              setDiscountType(null);
              setDiscountValue("");
            }}
            onAddDiscount={() => setDiscountType("percentage")}
          />
        </div>

        {/* Notes */}
        <div className="p-8 pt-6">
          <label
            htmlFor="notes-input"
            className="block text-[16px] font-bold text-gray-800 mb-3"
          >
            Notes
          </label>
          <textarea
            id="notes-input"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Add notes here"
            rows={2}
            disabled={restrictedMode}
            className="w-full p-4 border border-gray-300 rounded-xl text-[16px] outline-none focus:border-priori-purple resize-none placeholder-gray-400 disabled:bg-gray-50"
          />
        </div>
      </div>

      {/* Submit Error */}
      {submitError && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {submitError}
        </div>
      )}

      {/* Footer Actions */}
      <div className="flex justify-end items-center gap-4">
        <Button
          type="button"
          variant="outline"
          onClick={onPreview}
          disabled={!onPreview}
          className="px-8 py-3"
        >
          Preview
        </Button>

        {!restrictedMode && (
          <Button
            type="button"
            variant="primary"
            onClick={handleSubmit}
            loading={isLoading}
            className="px-8 py-3 flex items-center gap-2"
          >
            <Save size={18} /> Save &amp; Continue
          </Button>
        )}
      </div>
    </div>
  );
}

// DateField sub-component

interface DateFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  restrictedMode: boolean;
  colSpan?: string;
}

function DateField({
  label,
  value,
  onChange,
  error,
  restrictedMode,
  colSpan = "",
}: DateFieldProps) {
  return (
    <div className={`flex flex-col gap-2 ${colSpan}`}>
      <span className="text-gray-500 font-medium">{label}</span>
      <div className="flex items-center gap-2 relative w-fit">
        <span className="font-bold text-priori-purple whitespace-nowrap">
          {formatDisplayDate(value)}
        </span>
        {!restrictedMode && (
          <>
            <SquarePen
              size={20}
              className="text-priori-purple pointer-events-none shrink-0"
            />
            <input
              type="date"
              value={value}
              onChange={(e) => onChange(e.target.value)}
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
              aria-label={label}
            />
          </>
        )}
      </div>
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
}
