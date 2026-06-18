import { VendorSelector } from "@/components/modals/VendorSelector";
import { Button } from "@/components/ui/Button";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import {
  getComplianceRefLabel,
  getComplianceRefTooltip,
  resolveDefaultTerms,
  resolveOrgJurisdiction,
} from "@/lib/compliance";
import { ACCEPTED_UPLOAD_TYPES, CURRENCY_OPTIONS } from "@/lib/constants";
import { getTodayString } from "@/lib/dateUtils";
import { formatCurrency, formatDisplayDate } from "@/lib/utils";
import { PaperclipIcon, Plus, Save, SquarePen, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Divider } from "../ui/Divider";
import { DocumentOwnerHeader } from "./DocumentOwnerHeader";
import { LineItemsTable } from "./layout/line-items-table";
import {
  buildVatLabel,
  calculateTotals,
  createEmptyRow,
  type LineItemRow,
} from "./utils";

// Types

export interface PurchaseOrderLineItemPayload {
  itemName: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface PurchaseOrderPayload {
  vendorId: string;
  orderDate: string;
  deliveryDate?: string | null;
  currency: string;
  isRecurring: boolean;
  complianceRef?: string | null;
  notes?: string;
  termsAndConditions?: string | null;
  lineItems: PurchaseOrderLineItemPayload[];
  files?: File[];
}

export interface PurchaseOrderInitialData {
  poReference?: string;
  vendorId?: string;
  vendor?: {
    vendor_name: string;
    email?: string | null;
    phone_primary?: string | null;
    phone_secondary?: string | null;
    address?: string;
  };
  orderDate?: string;
  deliveryDate?: string | null;
  currency?: string;
  isRecurring?: boolean;
  complianceRef?: string | null;
  notes?: string;
  termsAndConditions?: string | null;
  lineItems?: {
    id?: string;
    itemName?: string;
    description: string;
    quantity: number;
    unitPrice: number;
    taxType?: string;
  }[];
}

interface PurchaseOrderEditorProps {
  initialData?: PurchaseOrderInitialData;
  onSave: (payload: PurchaseOrderPayload) => Promise<void>;
  isLoading: boolean;
  restrictedMode?: boolean;
}

// Component

export function PurchaseOrderEditor({
  initialData,
  onSave,
  isLoading,
  restrictedMode = false,
}: Readonly<PurchaseOrderEditorProps>) {
  // Org-scoped Settings defaults (PO-11), resolved from the persisted owner
  // profile with the built-in constants as fallback.
  const { profile } = useOwnerProfile();
  const orgJurisdiction = resolveOrgJurisdiction(profile?.jurisdiction);
  const orgDefaultTerms = resolveDefaultTerms(
    profile?.defaultTermsAndConditions
  );

  // State
  const [vendorId, setVendorId] = useState(initialData?.vendorId ?? "");
  const [orderDate, setOrderDate] = useState<string>(() => {
    const now = Date.now();
    return initialData?.orderDate ?? getTodayString(now);
  });
  // Delivery date is optional for a purchase order.
  const [deliveryDate, setDeliveryDate] = useState<string>(
    initialData?.deliveryDate ?? ""
  );

  const [currency, setCurrency] = useState(initialData?.currency ?? "KES");
  const [isRecurring, setIsRecurring] = useState(
    initialData?.isRecurring ?? false
  );
  const [complianceRef, setComplianceRef] = useState(
    initialData?.complianceRef ?? ""
  );
  const [notes, setNotes] = useState(initialData?.notes ?? "");
  // T&C is prefilled with the org default on new POs only (PO-11); on edit it
  // shows the PO's saved value (which may be intentionally blank) and the
  // default is never re-applied.
  const isEditing = !!initialData?.poReference;
  const [termsAndConditions, setTermsAndConditions] = useState(
    initialData?.termsAndConditions ?? (isEditing ? "" : orgDefaultTerms)
  );

  // Jurisdiction-aware Compliance Ref label/tooltip (PO-10), resolved from the
  // org's persisted jurisdiction so the form, View and PDF agree.
  const complianceRefLabel = getComplianceRefLabel(orgJurisdiction);
  const complianceRefTooltip = getComplianceRefTooltip(orgJurisdiction);
  const [queuedFiles, setQueuedFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isUploading] = useState(false);

  const [lineItems, setLineItems] = useState<LineItemRow[]>(() => {
    if (initialData?.lineItems?.length) {
      return initialData.lineItems.map((li) => ({
        key: li.id ?? Math.random().toString(),
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

  // Derived totals (client-side preview; server is the source of truth on save).
  const totals = useMemo(
    () => calculateTotals(lineItems, null, ""),
    [lineItems]
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

  // Validation: order date required; delivery date optional but, when set,
  // must be on or after the order date (mirrors the backend CHECK). At least
  // one line item with a name/description, positive quantity, non-negative
  // price.
  const validate = (): Record<string, string> => {
    const v: Record<string, string> = {};
    if (!vendorId) v.vendor = "Vendor is required";
    if (!orderDate) v.orderDate = "Order date is required";
    if (deliveryDate && orderDate && deliveryDate < orderDate) {
      v.deliveryDate = "Delivery date must be on or after the order date";
    }

    const validItems = lineItems.filter(
      (r) => r.description.trim() || r.itemName.trim()
    );
    if (validItems.length === 0) {
      v.lineItems = "At least one line item is required";
    }
    for (const item of validItems) {
      const qty = Number.parseFloat(item.quantity);
      const price = Number.parseFloat(item.unitPrice);
      if (!item.itemName.trim()) {
        v[`item_${item.key}_name`] = "Item name is required";
      }
      if (Number.isNaN(qty) || qty <= 0) {
        v[`item_${item.key}_qty`] = "Quantity must be greater than 0";
      }
      if (Number.isNaN(price) || price < 0) {
        v[`item_${item.key}_price`] = "Price must be 0 or greater";
      }
    }
    return v;
  };

  // Submit
  const handleSubmit = async () => {
    setSubmitError(null);

    const validationErrors = validate();
    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) return;

    const validItems = lineItems.filter(
      (r) => r.description.trim() || r.itemName.trim()
    );

    const items: PurchaseOrderLineItemPayload[] = validItems.map((r) => ({
      itemName: r.itemName.trim(),
      description: r.description.trim(),
      quantity: Number.parseFloat(r.quantity),
      unitPrice: Number.parseFloat(r.unitPrice),
      taxType: r.taxType || "no_tax",
    }));

    const payload: PurchaseOrderPayload = {
      vendorId,
      orderDate,
      deliveryDate: deliveryDate || null,
      currency,
      isRecurring,
      complianceRef: complianceRef.trim() || null,
      notes: notes.trim() || undefined,
      termsAndConditions: termsAndConditions.trim() || null,
      lineItems: items,
      files: queuedFiles.length > 0 ? queuedFiles : undefined,
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
            <div className="md:col-span-2">
              <DocumentOwnerHeader editable={!restrictedMode} />
            </div>

            <div className="flex flex-col gap-1">
              <h2 className="text-[22px] font-black text-gray-800 tracking-wider mb-1 uppercase">
                PURCHASE ORDER
              </h2>
              <VendorSelector
                label="To"
                initialVendorId={initialData?.vendorId}
                initialVendorName={initialData?.vendor?.vendor_name}
                initialVendorDetails={
                  initialData?.vendor
                    ? {
                      address: initialData.vendor.address,
                      phone:
                        initialData.vendor.phone_primary ??
                        initialData.vendor.phone_secondary ??
                        "",
                      email: initialData.vendor.email ?? "",
                    }
                    : null
                }
                onChange={setVendorId}
                restrictedMode={restrictedMode}
                error={errors.vendor}
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
                {initialData?.poReference ?? "Autogenerated"}
              </p>
            </div>

            {/* Order Date */}
            <DateField
              label="Order Date"
              value={orderDate}
              onChange={setOrderDate}
              error={errors.orderDate}
              restrictedMode={restrictedMode}
              colSpan="md:col-span-2"
            />

            {/* Delivery Date */}
            <DateField
              label="Delivery Date"
              value={deliveryDate}
              min={orderDate}
              onChange={setDeliveryDate}
              error={errors.deliveryDate}
              restrictedMode={restrictedMode}
              colSpan="md:col-span-2"
            />

            {/* Compliance Ref */}
            <div className="flex flex-col gap-2 min-w-0 md:col-span-2">
              <span className="text-gray-500 font-medium truncate" title={complianceRefTooltip}>
                {complianceRefLabel}
              </span>
              <label
                htmlFor="compliance-ref-input"
                className="flex items-center gap-2 w-fit max-w-full cursor-text"
              >
                <div className="relative flex items-center min-w-0">
                  <span className="invisible font-bold truncate block">
                    {complianceRef || "Enter"}
                  </span>
                  <input
                    id="compliance-ref-input"
                    type="text"
                    value={complianceRef}
                    onChange={(e) => setComplianceRef(e.target.value)}
                    placeholder="Enter"
                    disabled={restrictedMode}
                    title={complianceRefTooltip}
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
              <div className="flex items-center gap-2 relative w-fit min-w-16">
                <span className="font-bold text-priori-purple whitespace-nowrap">
                  {currency}
                </span>
                {!restrictedMode && !initialData?.poReference && (
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

            {/* Recurring */}
            <div className="flex flex-col gap-2 md:col-span-1">
              <span className="text-gray-500 font-medium">Recurring?</span>
              <label className="relative inline-flex items-center cursor-pointer shrink-0 mt-1">
                <input
                  type="checkbox"
                  className="sr-only peer"
                  checked={isRecurring}
                  onChange={(e) => setIsRecurring(e.target.checked)}
                  disabled={restrictedMode}
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-priori-purple"></div>
                <span className="ml-3 text-sm font-medium text-gray-900">
                  {isRecurring ? "Yes" : "No"}
                </span>
              </label>
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

          {/* Totals panel from PO */}
          <div className="w-full flex justify-end">
            <div className="min-w-80 flex flex-col gap-4 text-gray-800">
              <div className="flex justify-between items-center font-bold">
                <span>Subtotal</span>
                <span>
                  {formatCurrency(totals.subtotal, currency ?? "Ksh")}
                </span>
              </div>

              {(restrictedMode || totals.taxTotal > 0) && (
                <>
                  <Divider />
                  <div className="flex justify-between items-center text-gray-800">
                    <span>{vatLabel ?? "VAT"}</span>
                    <span>
                      {formatCurrency(totals.taxTotal, currency ?? "Ksh")}
                    </span>
                  </div>
                </>
              )}

              <Divider />

              <div className="flex justify-between items-center font-bold text-[16px] text-gray-900">
                <span>Total</span>
                <span>
                  {formatCurrency(totals.subtotal + totals.taxTotal, currency ?? "Ksh")}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Notes & Terms */}
        <div className="p-8 pt-6 flex flex-col gap-6">
          <div>
            <label
              htmlFor="notes-input"
              className="block text-[16px] font-bold text-gray-800 mb-2"
            >
              Notes
            </label>
            <textarea
              id="notes-input"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add notes here"
              rows={1}
              disabled={restrictedMode}
              className="w-full p-4 border border-gray-300 rounded-xl text-[16px] outline-none focus:border-priori-purple resize-none placeholder-gray-400 disabled:bg-gray-50"
            />
          </div>

          <div>
            <label
              htmlFor="terms-input"
              className="block text-[16px] font-bold text-gray-800 mb-3"
            >
              Terms &amp; Conditions
            </label>
            <textarea
              id="terms-input"
              value={termsAndConditions}
              onChange={(e) => setTermsAndConditions(e.target.value)}
              placeholder="Add terms & conditions here"
              rows={2}
              maxLength={2000}
              disabled={restrictedMode}
              className="w-full p-4 border border-gray-300 rounded-xl text-[16px] outline-none focus:border-priori-purple resize-none placeholder-gray-400 disabled:bg-gray-50"
            />
          </div>
        </div>
      </div>

      {!restrictedMode && (
        <div className="flex flex-col h-full min-h-30">
          <div className="flex items-center justify-between py-4">
            <h3 className="text-xl font-bold text-gray-800">Documents</h3>
            <Button
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
              className="flex items-center gap-2"
            >
              <Plus size={16} />{" "}
              {isUploading ? "Uploading..." : "Attach Document"}
            </Button>
          </div>

          <div className="flex-1 p-4 border-2 border-dashed border-gray-300 rounded-xl flex flex-col items-center justify-center bg-gray-50">
            {queuedFiles.length > 0 ? (
              <div className="w-full flex flex-col gap-2 max-h-32 overflow-y-auto">
                {queuedFiles.map((file, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between bg-white p-2 border border-gray-200 rounded"
                  >
                    <div className="flex items-center gap-3 overflow-hidden">
                      <PaperclipIcon size={24} className="text-gray-700" />
                      <div className="min-w-0">
                        <p
                          className="text-gray-800 text-[16px] truncate"
                          title={file.name}
                        >
                          {file.name}
                        </p>
                      </div>
                    </div>
                    <Button
                      onClick={() =>
                        setQueuedFiles((prev) =>
                          prev.filter((_, idx) => idx !== i)
                        )
                      }
                      aria-label="Delete document"
                      className="p-0 border-0 shadow-none bg-transparent flex items-center gap-2 text-gray-600 hover:text-priori-purple hover:bg-transparent"
                    >
                      <X size={24} />{" "}
                      <span className="text-[16px] text-gray-800">Delete</span>
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500 text-center mb-2">
                No documents attached yet.
                <br />
                They will be uploaded upon saving.
              </p>
            )}
            <input
              type="file"
              ref={fileInputRef}
              className="hidden"
              multiple
              accept={ACCEPTED_UPLOAD_TYPES}
              onChange={(e) => {
                if (e.target.files) {
                  setQueuedFiles((prev) => [
                    ...prev,
                    ...Array.from(e.target.files!),
                  ]);
                }
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => fileInputRef.current?.click()}
            >
              Attach Document
            </Button>
          </div>
        </div>
      )}

      {/* Submit Error */}
      {submitError && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {submitError}
        </div>
      )}

      {/* Footer Actions */}
      <div className="flex justify-end items-center gap-4 mt-2">
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
  min?: string;
}

export function DateField({
  label,
  value,
  onChange,
  error,
  restrictedMode,
  colSpan = "",
  min,
}: DateFieldProps) {
  return (
    <div className={`flex flex-col gap-2 ${colSpan}`}>
      <span className="text-gray-500 font-medium">{label}</span>
      <div className="flex items-center gap-2 relative w-fit">
        <span className="font-bold text-priori-purple whitespace-nowrap">
          {formatDisplayDate(value) || "Select date"}
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
              min={min}
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
