import { VendorSelector } from "@/components/modals/VendorSelector";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { useConfirm } from "@/hooks/useConfirm";
import {
  getComplianceRefLabel,
  getComplianceRefTooltip,
  resolveDefaultTerms,
  resolveOrgJurisdiction,
} from "@/lib/compliance";
import { getTodayString } from "@/lib/dateUtils";
import { formatCurrency, saveBlob } from "@/lib/utils";
import { deletePurchaseOrder, downloadPurchaseOrderPdf, markAsSentPurchaseOrder, sendPurchaseOrder } from "@/services/purchaseOrderApi";
import { CheckCircle, Download, Save, Send, Trash } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Divider } from "../ui/Divider";
import { Dropdown } from "../ui/Dropdown";
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
  const [error, setError] = useState<string | null>(null);


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
    setError(null);

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
      setError(
        err instanceof Error ? err.message : "Failed to save. Please try again."
      );
    }
  };

  const { showConfirm, ConfirmDialog } = useConfirm();
  const navigate = useNavigate();


  const handleSend = () => {
    if (!po) return;
    showConfirm({
      title: "Send purchase order?",
      description: `Send ${po.po_reference} to the vendor by email? It will be marked as Sent.`,
      confirmLabel: "Yes, send",
      onConfirm: async () => {
        try {
          await sendPurchaseOrder(po.id);
          fetchPurchaseOrder();
        } catch (err) {
          setError(err instanceof Error ? err.message : "Failed to send purchase order");
        }
      },
    });
  };

  const handleMarkAsSent = () => {
    if (!po) return;
    showConfirm({
      title: "Mark as sent?",
      description: "Are you sure you want to mark this item as sent?",
      confirmLabel: "Yes, mark as sent",
      onConfirm: async () => {
        try {
          await markAsSentPurchaseOrder(po.id);
          fetchPurchaseOrder();
        } catch (err) {
          setError(err instanceof Error ? err.message : "Failed to send purchase order");
        }
      },
    });
  };


  const handleDelete = () => {
    if (!po) return;
    showConfirm({
      title: "Delete purchase order?",
      description: `Delete ${po.po_reference}? This action is irreversible.`,
      confirmLabel: "Delete",
      variant: "danger",
      onConfirm: async () => {
        await deletePurchaseOrder(po.id);
        navigate("/purchase-orders");
      },
    });
  };

  /** Original PO PDF: full amount, no payments applied. */
  const handleDownloadPdf = async () => {
    if (!po) return;
    try {
      const blob = await downloadPurchaseOrderPdf(po.id);
      saveBlob(blob, `PurchaseOrder_${po.po_reference}.pdf`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to download PDF");
    }
  };

  // Build actions
  const actions = [];

  actions.push({
    key: "pdf",
    label: "Download PDF",
    icon: <Download size={16} />,
    onClick: handleDownloadPdf
  });

  if (status === "draft") {
    actions.push({
      key: "mark-sent"
      , label: "Mark as Sent",
      icon: <CheckCircle size={16} />,
      onClick: handleMarkSent
    });
  }

  actions.push({
    key: "send",
    label: "Send",
    icon: <Send size={16} />,
    onClick: () => setShowSendModal(true)
  });

  if (status === "draft") {
    actions.push({
      key: "delete",
      label: "Delete PO",
      icon: <Trash size={16} />,
      onClick: handleDelete
    });
  }



  return (
    <div className="flex flex-col gap-6 font-sans">
      <div className="flex justify-end items-center gap-4">
        {!restrictedMode && (
          <Button
            type="button"
            variant="primary"
            onClick={handleSubmit}
            loading={isLoading}
            className="px-5 py-4 flex items-center gap-2"
          >
            <Save size={18} /> Save &amp; Continue
          </Button>
        )}
        <Dropdown items={actions} className="flex items-center gap-2 px-5 py-4 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors" />
      </div>
      <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden shadow-sm">
        {/* Top Section */}
        <div className="p-6 flex justify-between">
          <DocumentOwnerHeader editable={!restrictedMode} />
          <h2 className="text-[22px] font-black text-priori-purple tracking-wider mb-1 uppercase">
            PURCHASE ORDER
          </h2>
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

              <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-4 items-center w-full max-w-130">
                {/* Reference */}
                <label className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap">
                  Reference
                </label>
                <Input
                  value={initialData?.poReference ?? "Autogenerated"}
                  disabled
                  readOnly
                />

                {/* Order Date */}
                <label
                  htmlFor="order-date"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Order Date
                </label>
                <Input
                  id="order-date"
                  type="date"
                  value={orderDate}
                  onChange={(e) => setOrderDate(e.target.value)}
                  disabled={restrictedMode}
                  error={errors.orderDate}
                />

                {/* Delivery Date (optional) */}
                <label
                  htmlFor="delivery-date"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Delivery Date
                </label>
                <Input
                  id="delivery-date"
                  type="date"
                  value={deliveryDate}
                  min={orderDate}
                  onChange={(e) => setDeliveryDate(e.target.value)}
                  disabled={restrictedMode}
                  error={errors.deliveryDate}
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
            onAddRow={addRow}
            onRemoveRow={removeRow}
            onUpdateRow={updateRow}
          />

          {/* Purchase Order Totals (no Amount Paid / Balance Due). */}
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
        <div className="px-8 pb-6">
          <label
            htmlFor="terms-input"
            className="block text-[16px] font-bold text-gray-800 mb-3"
          >
            Terms & Conditions
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

      {/* Submit Error */}
      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {error}
        </div>
      )}
    </div>
  );
}
