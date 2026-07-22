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
import { CalendarPicker } from "@/components/ui/CalendarPicker";
import {
  DEFAULT_DUE_DATE_DAYS,
} from "@/lib/constants";
import { lineTaxValidationError, vatRateValidationError } from "@/lib/taxUtils";
import { Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Divider } from "../ui/Divider";
import { DocumentOwnerHeader } from "./DocumentOwnerHeader";
import { DocumentTotalsPanel } from "./layout/document-totals";
import { LineItemsTable } from "./layout/line-items-table";
import {
  buildVatLabel,
  calcSubtotalVat,
  calculateTotals,
  createEmptyRow,
  roundMoney,
  validateDocument,
  type LineItemRow,
} from "./utils";

import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { addIsoDays, getDefaultDueDate, getTodayString } from "@/lib/dateUtils";
import { currencyOptions, type Currency } from "@/lib/enums";
import { Input } from "../ui/Input";
import { Toggle } from "../ui/Toggle";

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
  currency: Currency;
  // rfqNumber?: string;
  notes?: string;
  discountType?: "amount" | "percentage";
  discountAmount?: number;
  discountPercentage?: number;
  vatEnabled: boolean;
  vatRate: number | null | undefined;
  vatComplianceRef?: string | null;
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
    currency?: string | null;
  };
  transactionDate?: string;
  dueDate?: string;
  currency?: Currency;
  // rfqNumber?: string;
  notes?: string;
  vatEnabled?: boolean;
  vatRate?: number | null;
  vatComplianceRef?: string | null;
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
  const referenceLabel = type === "invoice" ? "INV" : "QT";

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

  const [currency, setCurrency] = useState<Currency>(
    (initialData?.customer?.currency as Currency | undefined) ??
    initialData?.currency ??
    "KES"
  );
  // const [rfqNumber, setRfqNumber] = useState(initialData?.rfqNumber ?? "");
  const [notes, setNotes] = useState(initialData?.notes ?? "");

  const [vatEnabled, setVatEnabled] = useState<boolean>(
    initialData?.vatEnabled ?? false
  );

  const isEditing = !!initialData?.documentReference;
  const { profile } = useOwnerProfile();


  const [vatRatePct, setVatRatePct] = useState<string>(() => {
    const fraction = initialData?.vatRate;
    if (fraction != null) {
      return String(Number((fraction * 100).toFixed(4)));
    }
    return "16";
  });

  const [vatComplianceRef, setVatComplianceRef] = useState<string>(
    initialData?.vatComplianceRef ?? ""
  );

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

  const transactionDateTouched = useRef(false);

  useEffect(() => {
    if (isEditing || transactionDateTouched.current || !profile?.reportingDate) return;
    setTransactionDate(profile.reportingDate);
    setDueDate(addIsoDays(profile.reportingDate, DEFAULT_DUE_DATE_DAYS));
  }, [isEditing, profile?.reportingDate]);

  // Default new documents to the owner's (our own) VAT settings: the
  // toggle, the rate and the compliance ref all seed from the profile
  // until the user touches them. Editing an existing document never
  // re-defaults, and the seed runs at most once.
  const vatRefTouched = useRef(false);
  const vatDefaulted = useRef(false);
  useEffect(() => {
    if (isEditing || vatRefTouched.current || vatDefaulted.current) return;
    if (initialData?.vatComplianceRef != null) return;
    if (initialData?.vatRate != null || initialData?.vatEnabled != null) return;
    if (!profile) return;
    vatDefaulted.current = true;
    if (profile.taxPin) setVatComplianceRef(profile.taxPin);
    if (profile.vatRate) {
      setVatRatePct(String(Number((profile.vatRate * 100).toFixed(4))));
    }
    if (profile.vatEnabled != null) setVatEnabled(Boolean(profile.vatEnabled));
  }, [
    isEditing,
    initialData?.vatComplianceRef,
    initialData?.vatEnabled,
    initialData?.vatRate,
    profile,
  ]);


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

  const vatRateError = vatEnabled
    ? vatRateValidationError(vatRatePct, transactionDate)
    : undefined;

  const vatRateFraction = useMemo(() => {
    const pct = Number.parseFloat(vatRatePct);
    return Number.isFinite(pct) ? pct / 100 : 0;
  }, [vatRatePct]);

  const vatLabel = useMemo(
    () =>
      vatEnabled
        ? buildVatLabel(vatRateFraction, vatComplianceRef)
        : "VAT",
    [vatEnabled, vatRateFraction, vatComplianceRef]
  );

  // Derived totals (memoized). When the document-level VAT toggle is on,
  // VAT replaces per-line tax and is charged once on the discounted base,
  // mirroring the backend computation exactly.
  const totals = useMemo(() => {
    const base = calculateTotals(lineItems, discountType, discountValue);
    const taxTotal = vatEnabled
      ? calcSubtotalVat(
        base.subtotal - base.discountAmount,
        vatEnabled,
        vatRateFraction
      )
      : base.taxTotal;

    return {
      subtotal: base.subtotal,
      discountAmount: base.discountAmount,
      taxTotal,
      totalDue: roundMoney(base.subtotal - base.discountAmount + taxTotal),
    };
  }, [lineItems, discountType, discountValue, vatEnabled, vatRateFraction]);


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

    const validItems = lineItems.filter(
      (r) => r.description.trim() || r.itemName.trim()
    );

    if (vatRateError) validationErrors.vatRate = vatRateError;
    if (!vatEnabled) {
      for (const item of validItems) {
        const taxError = lineTaxValidationError(item.taxType, transactionDate);
        if (taxError) validationErrors[`item_${item.key}_tax`] = taxError;
      }
    }

    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) return;

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
      vatEnabled,
      vatRate: vatEnabled ? vatRateFraction : null,
      vatComplianceRef: vatComplianceRef.trim() || null,
      lineItems: items,
      // No RFQ input is rendered yet; pass the persisted value through so
      // an update never silently drops it.
      // rfqNumber: initialData?.rfqNumber,
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
        <div className="p-6 flex justify-between">
          <DocumentOwnerHeader editable={!restrictedMode} />
          <h2 className="text-[22px] font-black text-priori-purple tracking-wider mb-1 uppercase">
            {label}
          </h2>
        </div>

        <div className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Document To (Customer Selector) */}
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
              onChange={(id: string, cur?: string) => {
                setCustomerId(id);

                if (cur && currencyOptions.some((option) => option.value === cur)) {
                  setCurrency(cur as Currency);
                }
              }}
              restrictedMode={restrictedMode}
              error={errors.customer}
            />


            {/* Metadata Row */}
            <div className="flex flex-col gap-2 items-end">
              <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-4 items-center w-full max-w-130">

                {/* Reference */}
                <label className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap">
                  {`${referenceLabel} No.`}
                </label>
                <Input
                  value={initialData?.documentReference ?? "Autogenerated"}
                  disabled
                  readOnly
                />

                {/* Transaction Date */}
                <label
                  htmlFor="order-date"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Transaction Date
                </label>
                <CalendarPicker
                  id="transaction-date"
                  variant="form"
                  value={transactionDate}
                  onChange={(d) => {
                    setTransactionDate(d);
                    transactionDateTouched.current = true;
                  }}
                  disabled={restrictedMode}
                  error={errors.transactionDate}
                  aria-label="Transaction date"
                  today={profile?.reportingDate}
                />

                {/* Due Date */}
                <label
                  htmlFor="due-date"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Due Date
                </label>
                <CalendarPicker
                  id="due-date"
                  variant="form"
                  value={dueDate}
                  onChange={(d) => setDueDate(d)}
                  disabled={restrictedMode}
                  error={errors.dueDate}
                  aria-label="Due date"
                  today={profile?.reportingDate}
                />

                {/* RFQ/RFP Number */}
                {/* <label
                  htmlFor="rfq-input"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  RFQ/RFP Number
                </label>
                <Input
                  id="rfq-input"
                  type="text"
                  value={rfqNumber}
                  onChange={(e) => setRfqNumber(e.target.value)}
                  disabled={restrictedMode}
                  error={errors.rfqNumber}
                /> */}

                {/* VAT Toggle */}
                <label
                  htmlFor="vat-enabled"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Add VAT
                </label>

                <Toggle
                  id="vat-enabled"
                  checked={vatEnabled}
                  onChange={setVatEnabled}
                  disabled={restrictedMode}
                  error={errors.vatEnabled}
                  aria-label="Add VAT"
                />
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
            enableInlineTax={!vatEnabled}
            taxPointDate={transactionDate}
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
            vatEnabled={vatEnabled}
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
        <div className="px-8 py-6">
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

        {/* Submit Error */}
        {
          submitError && (
            <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
              {submitError}
            </div>
          )
        }

        {/* Footer Actions */}
        <div className="flex justify-end items-center gap-4 px-8 py-6">
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
      </div >
    </div >
  );
}