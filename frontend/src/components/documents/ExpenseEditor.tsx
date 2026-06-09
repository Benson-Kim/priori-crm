import { VendorSelector } from "@/components/modals/VendorSelector";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { CURRENCY_OPTIONS } from "@/lib/constants";
import { DocumentOwnerHeader } from "./DocumentOwnerHeader";
import { formatCurrency } from "@/lib/utils";
import { PaperclipIcon, Plus, Save, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Divider } from "../ui/Divider";
import { LineItemsTable } from "./layout/line-items-table";
import {
  buildVatLabel,
  calculateTotals,
  createEmptyRow,
  validateDocument,
  type LineItemRow,
} from "./utils";
import {
  getTodayString,
  getDefaultDueDate,
  isDueDateBeforeTransactionDate,
  addDays,
} from "@/lib/dateUtils";

// Types

export interface ExpenseLineItemPayload {
  itemName: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxType: string;
}

export interface ExpensePayload {
  vendorId: string;
  expenseDate: string;
  dueDate: string;
  currency: string;
  isRecurring: boolean;
  notes?: string;
  lineItems: ExpenseLineItemPayload[];
  files?: File[];
}

export interface ExpenseInitialData {
  expenseReference?: string;
  vendorId?: string;
  vendor?: {
    vendor_name: string;
    email?: string | null;
    phone_primary?: string | null;
    phone_secondary?: string | null;
    address?: string;
  };
  expenseDate?: string;
  dueDate?: string;
  currency?: string;
  isRecurring?: boolean;
  notes?: string;
  lineItems?: {
    id?: string;
    itemName?: string;
    description: string;
    quantity: number;
    unitPrice: number;
    taxType?: string;
  }[];
}

interface ExpenseEditorProps {
  initialData?: ExpenseInitialData;
  onSave: (payload: ExpensePayload) => Promise<void>;
  isLoading: boolean;
  restrictedMode?: boolean;
}

// Component

export function ExpenseEditor({
  initialData,
  onSave,
  isLoading,
  restrictedMode = false,
}: Readonly<ExpenseEditorProps>) {
  // State
  const [vendorId, setVendorId] = useState(initialData?.vendorId ?? "");
  const [expenseDate, setExpenseDate] = useState<string>(() => {
    const now = Date.now();
    return initialData?.expenseDate ?? getTodayString(now);
  });

  const [dueDate, setDueDate] = useState<string>(() => {
    const now = Date.now();
    return initialData?.dueDate ?? getDefaultDueDate(30, now);
  });

  function handleExpenseDateChange(newDate: string) {
    setExpenseDate(newDate);

    // Auto-correct due date if it would become invalid
    if (isDueDateBeforeTransactionDate(dueDate, newDate)) {
      setDueDate(addDays(newDate, 30));
    }
  }

  const [currency, setCurrency] = useState(initialData?.currency ?? "KES");
  const [isRecurring, setIsRecurring] = useState(
    initialData?.isRecurring ?? false
  );
  const [notes, setNotes] = useState(initialData?.notes ?? "");
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

  // Derived totals
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

  // Submit
  const handleSubmit = async () => {
    setSubmitError(null);

    const validationErrors = validateDocument(
      vendorId,
      expenseDate,
      dueDate,
      lineItems,
      true,
      "Vendor"
    );

    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) return;

    const validItems = lineItems.filter(
      (r) => r.description.trim() || r.itemName.trim()
    );

    const items: ExpenseLineItemPayload[] = validItems.map((r) => ({
      itemName: r.itemName.trim(),
      description: r.description.trim(),
      quantity: Number.parseFloat(r.quantity),
      unitPrice: Number.parseFloat(r.unitPrice),
      taxType: r.taxType || "no_tax",
    }));

    const payload: ExpensePayload = {
      vendorId,
      expenseDate,
      dueDate,
      currency,
      isRecurring,
      lineItems: items,
      notes: notes.trim() || undefined,
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
      <div className="flex justify-end items-center gap-4">
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
      <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden shadow-sm">
        {/* Top Section */}
        <div className="p-6">
          <DocumentOwnerHeader editable={!restrictedMode} />
        </div>

        <div className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
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

            {/* Metadata */}
            <div className="flex flex-col gap-2 items-end">
              <h2 className="text-[22px] font-black text-priori-purple tracking-wider mb-1 uppercase">
                EXPENSE
              </h2>
              {/* Reference */}
              <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-4 items-center w-full max-w-130">
                <label className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap">
                  Reference
                </label>
                <Input
                  value={initialData?.expenseReference ?? "Autogenerated"}
                  disabled
                  readOnly
                  className=""
                />

                {/* Expense Date */}
                <label
                  htmlFor="expense-date"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Expense Date
                </label>
                <Input
                  id="expense-date"
                  type="date"
                  value={expenseDate}
                  onChange={(e) => handleExpenseDateChange(e.target.value)}
                  disabled={restrictedMode}
                  error={errors.transactionDate}
                />

                {/* Due Date */}

                <label
                  htmlFor="due-date"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Due Date
                </label>
                <Input
                  id="due-date"
                  type="date"
                  value={dueDate}
                  min={expenseDate}
                  onChange={(e) => setDueDate(e.target.value)}
                  disabled={restrictedMode}
                  error={errors.dueDate}
                />

                {/* Currency */}

                <label
                  htmlFor="currency-select"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Currency
                </label>
                <Select
                  id="currency-select"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  disabled={restrictedMode || !!initialData?.expenseReference}
                  options={CURRENCY_OPTIONS}
                />

                {/* Recurring Bill */}
                <span className="text-base font-bold leading-6 text-gray-800 text-right flex-1 whitespace-nowrap">
                  Recurring Bill?
                </span>
                <label className="relative inline-flex items-center text-right cursor-pointer shrink-0">
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

          {/* Expense Totals */}
          <div className="w-full flex justify-end">
            <div className="min-w-80 flex flex-col gap-4 text-gray-800">
              {/* Subtotal */}
              <div className="flex justify-between items-center font-bold">
                <span>Subtotal</span>
                <span>
                  {formatCurrency(totals.subtotal, currency ?? "Ksh")}
                </span>
              </div>

              {/* VAT - Only show if restricted mode (view mode) or if there is actually tax */}
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

              {/* Total Due */}
              <div className="flex justify-between items-center font-bold text-[16px] text-gray-900">
                <span>Total Due</span>
                <span>
                  {formatCurrency(totals.totalDue, currency ?? "Ksh")}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Notes & Attachments */}
        <div className="p-6">
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
            className="w-full p-4 border border-gray-300 rounded-xl text-[16px] outline-none focus:border-priori-purple resize-none placeholder-gray-400 disabled:bg-gray-50 h-full min-h-30"
          />
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
    </div>
  );
}
