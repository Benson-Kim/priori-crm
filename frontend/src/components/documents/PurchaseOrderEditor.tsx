import { VendorSelector } from "@/components/modals/VendorSelector";
import { Button } from "@/components/ui/Button";
import { CalendarPicker } from "@/components/ui/CalendarPicker";
import { Input } from "@/components/ui/Input";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { useConfirm } from "@/hooks/useConfirm";
import {
  resolveDefaultTerms,
} from "@/lib/compliance";
import { getTodayString } from "@/lib/dateUtils";
import { lineTaxValidationError, vatRateValidationError } from "@/lib/taxUtils";
import { formatCurrency, saveBlob } from "@/lib/utils";
import {
  deletePurchaseOrder,
  downloadPurchaseOrderPdf,
  markAsSentPurchaseOrder,
  sendPurchaseOrder,
  type PurchaseOrderResponse,
} from "@/services/purchaseOrderApi";
import { CheckCircle, Download, Save, Send, Trash } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Divider } from "../ui/Divider";
import { Dropdown, type DropdownItem } from "../ui/Dropdown";
import { Toggle } from "../ui/Toggle";
import { DocumentOwnerHeader } from "./DocumentOwnerHeader";
import { LineItemsTable } from "./layout/line-items-table";
import {
  buildVatLabel,
  calcSubtotalVat,
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
  notes?: string;
  termsAndConditions?: string | null;
  lineItems: PurchaseOrderLineItemPayload[];
  vatEnabled: boolean;
  vatRate: number | null | undefined;
  vatComplianceRef?: string | null;
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
    currency?: string | null;
  };
  orderDate?: string;
  deliveryDate?: string | null;
  notes?: string;
  termsAndConditions?: string | null;
  vatEnabled?: boolean;
  vatRate?: number | null;
  vatComplianceRef?: string | null;
  lineItems?: {
    id?: string;
    itemName?: string;
    description: string;
    quantity: number;
    unitPrice: number;
    taxType?: string;
  }[];
}

/** Action selectable from the Save & Continue dropdown. */
type EditorAction = "pdf" | "mark-sent" | "send" | "delete";

interface PurchaseOrderEditorProps {
  initialData?: PurchaseOrderInitialData;
  /**
   * Persist the PO. Pass `{ skipNavigate: true }` to keep the user on the
   * editor and resolve the saved PO so a follow-up action can run against it.
   */
  onSave: (
    payload: PurchaseOrderPayload,
    options?: { skipNavigate?: boolean }
  ) => Promise<PurchaseOrderResponse>;
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
  // Org-scoped Settings defaults, resolved from the persisted owner
  // profile with the built-in constants as fallback.
  const { profile } = useOwnerProfile();
  const navigate = useNavigate();
  const { showConfirm, ConfirmDialog } = useConfirm();
  const orgDefaultTerms = resolveDefaultTerms(
    profile?.defaultTermsAndConditions
  );

  // State
  const [vendorId, setVendorId] = useState(initialData?.vendorId ?? "");
  const [orderDate, setOrderDate] = useState<string>(() => {
    const now = Date.now();
    return initialData?.orderDate ?? getTodayString(now);
  });

  const orderDateTouched = useRef(false);

  useEffect(() => {
    const isEditingDoc = !!initialData?.poReference;
    if (isEditingDoc || orderDateTouched.current || !profile?.reportingDate) return;
    setOrderDate(profile.reportingDate);
  }, [initialData?.poReference, profile?.reportingDate]);

  // Delivery date is optional for a purchase order.
  const [deliveryDate, setDeliveryDate] = useState<string>(
    initialData?.deliveryDate ?? ""
  );

  // Currency follows the selected vendor. Seeded from the initial vendor (edit
  // flow) and updated instantly when a vendor is picked in the selector, so the
  // totals preview shows the right currency before the server round-trip. The
  // server remains the source of truth on save (currency is vendor-derived).
  const [currency, setCurrency] = useState<string>(
    initialData?.vendor?.currency ?? "KES"
  );

  const [notes, setNotes] = useState(initialData?.notes ?? "");

  const [vatEnabled, setVatEnabled] = useState<boolean>(
    initialData?.vatEnabled ?? false
  );
  // Rate held as a whole-number percent string for the selector (e.g. "16").
  const [vatRatePct, setVatRatePct] = useState<string>(() => {
    const fraction = initialData?.vatRate;
    if (fraction != null) {
      // Preserve existing fractional rates (e.g. 0.1067 -> "10.67").
      // Round to 4 decimal places to match migration backfill precision
      // but keep the fractional value so a notes-only save does not
      // silently re-persist a rounded integer percent.
      return String(Number((fraction * 100).toFixed(4)));
    }
    return "16";
  });
  // True once the user picks a different rate from the dropdown. On an
  // existing PO whose persisted rate is a non-integer percent (e.g. the
  // backfilled blended rate 0.1067), a notes-only save must NOT re-persist
  // the rounded selector value. When the ref stays false the payload omits
  // vatRate entirely so the backend keeps the original fraction.
  const vatRateDirty = useRef(false);
  // VAT compliance ref defaults from the owner profile's tax PIN on a new PO
  // (editable). On an existing PO the persisted value wins.
  const isEditingDoc = !!initialData?.poReference;
  const [vatComplianceRef, setVatComplianceRef] = useState<string>(
    initialData?.vatComplianceRef ?? ""
  );
  // The owner profile loads asynchronously, so the tax PIN is usually absent
  // on first render. Seed the compliance ref from it once it arrives, but
  // only for a NEW PO and only while the field is still untouched, so we
  // never clobber a persisted value or something the user has typed.
  // Render-time adjustment (react.dev "adjusting state when props change")
  // instead of an effect: the seed re-applies whenever the profile's VAT
  // values change (the effect's dependency list previously missed
  // profile.vatRate — react-hooks/exhaustive-deps). The old vatRefTouched
  // ref was never written anywhere, so dropping it changes nothing.
  const vatProfileKey = `${profile?.taxPin ?? ""}|${profile?.vatRate ?? ""}`;
  const [seededVatProfileKey, setSeededVatProfileKey] = useState<string | null>(null);
  if (
    !isEditingDoc &&
    initialData?.vatComplianceRef == null &&
    initialData?.vatRate == null &&
    (profile?.taxPin || profile?.vatRate) &&
    seededVatProfileKey !== vatProfileKey
  ) {
    setSeededVatProfileKey(vatProfileKey);
    if (profile?.taxPin) setVatComplianceRef(profile.taxPin);
    if (profile?.vatRate) setVatRatePct(profile.vatRate * 100 + '%');
  }

  const isEditing = !!initialData?.poReference;
  const [termsAndConditions, setTermsAndConditions] = useState(
    initialData?.termsAndConditions ?? (isEditing ? "" : orgDefaultTerms)
  );

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
  // True while a dropdown action's save-then-act flow is running, so the
  // dropdown trigger can be disabled and the user can't double-submit.
  const [isActionRunning, setIsActionRunning] = useState(false);


  // VAT rate as a fraction for computation (e.g. 0.16).
  const vatRateFraction = useMemo(() => {
    const pct = Number.parseFloat(vatRatePct);
    return Number.isFinite(pct) ? pct / 100 : 0;
  }, [vatRatePct]);

  // Derived totals (client-side preview; server is the source of truth on save
  const totals = useMemo(() => {
    const base = calculateTotals(lineItems, null, "");
    const taxTotal = calcSubtotalVat(base.subtotal, vatEnabled, vatRateFraction);
    return { subtotal: base.subtotal, taxTotal };
  }, [lineItems, vatEnabled, vatRateFraction]);

  const vatLabel = useMemo(
    () =>
      vatEnabled
        ? buildVatLabel(vatRateFraction, vatComplianceRef)
        : "VAT",
    [vatEnabled, vatRateFraction, vatComplianceRef]
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

    const vatRateError = vatEnabled ? vatRateValidationError(vatRatePct, orderDate) : undefined;
    if (vatRateError) v.vatRate = vatRateError;

    const validItems = lineItems.filter(
      (r) => r.description.trim() || r.itemName.trim()
    );

    if (!vatEnabled) {
      for (const item of validItems) {
        const taxError = lineTaxValidationError(item.taxType, orderDate);
        if (taxError) v[`item_${item.key}_tax`] = taxError;
      }
    }

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

  // Validate the form and build the save payload. Returns null (and sets the
  // field errors) when the form is invalid, so both Save & Continue and the
  // action dropdown share one validation path.
  const buildPayload = (): PurchaseOrderPayload | null => {
    const validationErrors = validate();
    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) return null;

    const validItems = lineItems.filter(
      (r) => r.description.trim() || r.itemName.trim()
    );

    // Preserve per-line tax type when present. Some historical POs or
    // user-edited line items may include inline tax rows; if present,
    // preserve them rather than silently discarding on save.
    const items: PurchaseOrderLineItemPayload[] = validItems.map((r) => ({
      itemName: r.itemName.trim(),
      description: r.description.trim(),
      quantity: Number.parseFloat(r.quantity),
      unitPrice: Number.parseFloat(r.unitPrice),
      taxType: r.taxType || "no_tax",
    }));

    return {
      vendorId,
      orderDate,
      deliveryDate: deliveryDate || null,
      notes: notes.trim() || undefined,
      termsAndConditions: termsAndConditions.trim() || null,
      lineItems: items,
      vatEnabled,
      // On an existing PO, only send the rate when the user actually changed
      // it, so a non-integer persisted fraction (e.g. 0.1067 from the
      // migration backfill) is never silently rounded to the nearest integer
      // percent on a notes-only save.
      vatRate: vatEnabled
        ? (!isEditing || vatRateDirty.current ? vatRateFraction : undefined)
        : null,
      vatComplianceRef: vatComplianceRef.trim() || null,
    };
  };

  // Submit (plain Save & Continue): the form hook navigates to the list.
  const handleSubmit = async () => {
    setError(null);
    const payload = buildPayload();
    if (!payload) return;

    try {
      await onSave(payload);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save. Please try again."
      );
    }
  };

  // Save-then-act for the dropdown actions. The PO is persisted first (created
  // on a new PO), then the chosen action runs against the saved id reusing the
  // existing API client. Destructive / sending actions confirm before saving.
  const runAction = async (action: EditorAction) => {
    setError(null);
    const payload = buildPayload();
    if (!payload) return;

    const execute = async () => {
      setIsActionRunning(true);
      try {
        const saved = await onSave(payload, { skipNavigate: true });
        switch (action) {
          case "pdf": {
            const blob = await downloadPurchaseOrderPdf(saved.id);
            saveBlob(blob, `PurchaseOrder_${saved.po_reference}.pdf`);
            navigate(`/purchase-orders/${saved.id}`);
            break;
          }
          case "mark-sent": {
            await markAsSentPurchaseOrder(saved.id);
            navigate(`/purchase-orders/${saved.id}`);
            break;
          }
          case "send": {
            await sendPurchaseOrder(saved.id);
            navigate(`/purchase-orders/${saved.id}`);
            break;
          }
          case "delete": {
            await deletePurchaseOrder(saved.id);
            navigate("/purchase-orders");
            break;
          }
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to complete the action. Please try again."
        );
      } finally {
        setIsActionRunning(false);
      }
    };

    if (action === "send") {
      showConfirm({
        title: "Save and send purchase order?",
        description:
          "The purchase order will be saved and emailed to the vendor, then marked as Sent.",
        confirmLabel: "Yes, send",
        onConfirm: execute,
      });
      return;
    }
    if (action === "mark-sent") {
      showConfirm({
        title: "Save and mark as sent?",
        description:
          "The purchase order will be saved and marked as Sent without emailing the vendor.",
        confirmLabel: "Yes, mark as sent",
        onConfirm: execute,
      });
      return;
    }
    if (action === "delete") {
      showConfirm({
        title: "Save and delete purchase order?",
        description:
          "The purchase order will be saved and then permanently deleted. This action cannot be undone.",
        confirmLabel: "Yes, delete it",
        variant: "danger",
        onConfirm: execute,
      });
      return;
    }
    // Download as PDF needs no confirmation.
    await execute();
  };

  // Document actions (Download PDF / Mark as Sent / Send / Delete) act on a
  // PERSISTED purchase order. The PO does not exist until saved, so each
  // action saves first (creating the PO) and then runs against the saved id
  // via runAction. The dropdown is disabled in restricted (read-only) mode or
  // while an action is already running.
  const actions: DropdownItem[] = [
    {
      key: "pdf",
      label: "Download as PDF",
      icon: <Download size={16} />,
      onClick: () => void runAction("pdf"),
    },
    {
      key: "mark-sent",
      label: "Mark as Sent",
      icon: <CheckCircle size={16} />,
      onClick: () => void runAction("mark-sent"),
    },
    {
      key: "send",
      label: "Send",
      icon: <Send size={16} />,
      onClick: () => void runAction("send"),
    },
    {
      key: "delete",
      label: "Delete",
      icon: <Trash size={16} />,
      danger: true,
      onClick: () => void runAction("delete"),
    },
  ];


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
        {!restrictedMode && (
          <div
            title="Saves the purchase order first, then runs the selected action"
          >
            <Dropdown
              items={actions}
              disabled={isLoading || isActionRunning}
              className="flex items-center gap-2 px-5 py-4 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
            />
          </div>
        )}
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
              onChange={(id: string, cur?: string) => {
                setVendorId(id);
                if (cur) setCurrency(cur);
              }}
              restrictedMode={restrictedMode}
              error={errors.vendor}
            />

            {/* Metadata */}
            <div className="flex flex-col gap-2 items-end">

              <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-4 items-center w-full max-w-130">
                {/* Reference */}
                <label className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap">
                  PO No.
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
                <CalendarPicker
                  id="order-date"
                  variant="form"
                  value={orderDate}
                  onChange={(d) => {
                    setOrderDate(d);
                    orderDateTouched.current = true;
                  }}
                  disabled={restrictedMode}
                  error={errors.orderDate}
                  aria-label="Order date"
                  today={profile?.reportingDate}
                />

                {/* Delivery Date (optional) */}
                <label
                  htmlFor="delivery-date"
                  className="text-base font-bold leading-6 text-gray-800 text-right whitespace-nowrap"
                >
                  Delivery Date
                </label>
                <CalendarPicker
                  id="delivery-date"
                  variant="form"
                  value={deliveryDate}
                  min={orderDate}
                  onChange={(d) => setDeliveryDate(d)}
                  disabled={restrictedMode}
                  error={errors.deliveryDate}
                  aria-label="Delivery date"
                  today={profile?.reportingDate}
                />


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
            onAddRow={addRow}
            onRemoveRow={removeRow}
            onUpdateRow={updateRow}
            enableInlineTax={false}
            taxPointDate={orderDate}
          />

          {/* Purchase Order Totals (no Amount Paid / Balance Due). */}
          <div className="w-full flex justify-end">
            <div className="min-w-80 flex flex-col gap-4 text-gray-800">
              <div className="flex justify-between items-center font-bold">
                <span>Subtotal</span>
                <span>
                  {formatCurrency(totals.subtotal, currency)}
                </span>
              </div>

              {(vatEnabled || restrictedMode || totals.taxTotal > 0) && (
                <>
                  <Divider />

                  <div className="flex justify-between items-center text-gray-800">
                    <span>{vatLabel}</span>
                    <span>{formatCurrency(totals.taxTotal, currency)}</span>
                  </div>
                </>
              )}

              <Divider />

              <div className="flex justify-between items-center font-bold text-[16px] text-gray-900">
                <span>Total</span>
                <span>
                  {formatCurrency(totals.subtotal + totals.taxTotal, currency)}
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

      {/* Confirmation dialog for the save-then-act dropdown actions. */}
      {ConfirmDialog}
    </div>
  );
}
