import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { ACCEPTED_UPLOAD_TYPES, CURRENCY_OPTIONS, DEFAULT_CURRENCY } from "@/lib/constants";
import { formatCurrency } from "@/lib/utils";
import { recordPayment as recordExpensePayment, type ExpensePaymentPayload } from "@/services/expenseApi";
import { recordPayment as recordInvoicePayment, type PaymentCreatePayload as InvoicePaymentPayload } from "@/services/invoiceApi";
import {
    deletePurchaseOrderDocument,
    recordPurchaseOrderPayment,
    updatePurchaseOrderPayment,
    uploadPurchaseOrderDocument,
    type PurchaseOrderDocument,
    type PurchaseOrderPayment,
    type PurchaseOrderPaymentPayload,
} from "@/services/purchaseOrderApi";
import { CreditCard, Paperclip, Plus, Trash2, X } from "lucide-react";
import { startTransition, useEffect, useRef, useState } from "react";

interface RecordPaymentModalProps {
    isOpen: boolean;
    onClose: () => void;
    entityId: string;
    entityType: "invoice" | "expense" | "purchaseOrder";
    balanceDue: number;
    /** For purchase orders this is the PO currency (the balance currency). */
    currency: string;
    prefillAmount?: number;
    reference?: string;
    /**
     * When supplied, the modal edits this existing payment (purchase orders
     * only) instead of recording a new one: fields prefill from it and Save
     * calls updatePurchaseOrderPayment. The editable balance is the live
     * balanceDue plus this payment's own amount (removing it frees that).
     */
    editPayment?: PurchaseOrderPayment | null;
    /**
     * All PO documents — used in edit mode to resolve and display documents
     * already linked to this payment. Callers pass the full PO document array;
     * the modal filters to the relevant subset internally.
     */
    existingDocuments?: PurchaseOrderDocument[];
    onSuccess: () => void;
}

const PAYMENT_METHODS = [
    { value: "cash", label: "Cash" },
    { value: "bank_transfer", label: "Bank Transfer" },
    { value: "check", label: "Check" },
    { value: "card", label: "Card" },
    { value: "mobile_money", label: "Mobile Money" },
    { value: "other", label: "Other" },
];

/** Read a document's classification, tolerating older rows without the field. */
function docTypeOf(doc: PurchaseOrderDocument): string {
    return (doc as { document_type?: string | null }).document_type ?? "other";
}

export function RecordPaymentModal({
    isOpen,
    onClose,
    entityId,
    entityType,
    balanceDue,
    currency,
    prefillAmount,
    editPayment,
    existingDocuments = [],
    onSuccess
}: RecordPaymentModalProps) {
    const isEditing = !!editPayment;
    const isPurchaseOrder = entityType === "purchaseOrder";
    // The PO currency is the balance currency; payments may be made in a
    // different currency and converted via the exchange rate.
    const poCurrency = currency || DEFAULT_CURRENCY;

    const [amount, setAmount] = useState(String(prefillAmount ?? balanceDue));
    const [paymentDate, setPaymentDate] = useState(new Date().toISOString().split("T")[0]);
    const [paymentMethod, setPaymentMethod] = useState("bank_transfer");
    const [reference, setReference] = useState("");
    const [invoiceNumber, setInvoiceNumber] = useState("");
    const [paymentCurrency, setPaymentCurrency] = useState(poCurrency);
    const [exchangeRate, setExchangeRate] = useState("1");
    const [notes, setNotes] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // New files to upload on submit, split by classification.
    const [invoiceFiles, setInvoiceFiles] = useState<File[]>([]);
    const [popFiles, setPopFiles] = useState<File[]>([]);
    // IDs of existing documents the user marked for deletion.
    const [deletedDocIds, setDeletedDocIds] = useState<Set<string>>(new Set());
    const invoiceInputRef = useRef<HTMLInputElement>(null);
    const popInputRef = useRef<HTMLInputElement>(null);

    // Only the purchase-order payment API supports linking proof-of-payment
    // documents and multi-currency, so the extra controls are PO-only.
    const supportsAttachments = isPurchaseOrder;
    const currencyDiffers = isPurchaseOrder && paymentCurrency !== poCurrency;

    // Existing documents linked to this payment (edit mode only), split by type.
    const relatedDocuments = isEditing
        ? existingDocuments.filter(
            (d) =>
                (d.payment_id === editPayment.id || d.id === editPayment.document_id) &&
                !deletedDocIds.has(d.id)
        )
        : [];
    const relatedInvoiceDocs = relatedDocuments.filter((d) => docTypeOf(d) === "invoice");
    const relatedPopDocs = relatedDocuments.filter((d) => docTypeOf(d) !== "invoice");

    useEffect(() => {
        if (isOpen) {
            startTransition(() => {
                if (editPayment) {
                    setAmount(String(editPayment.amount));
                    setPaymentDate(
                        editPayment.payment_date
                            ? String(editPayment.payment_date).split("T")[0]
                            : new Date().toISOString().split("T")[0]
                    );
                    setReference(editPayment.reference ?? "");
                    setInvoiceNumber(
                        (editPayment as { invoice_number?: string | null }).invoice_number ?? ""
                    );
                    setPaymentCurrency(
                        (editPayment as { currency?: string | null }).currency ?? poCurrency
                    );
                    setExchangeRate(
                        String((editPayment as { exchange_rate?: number | string | null }).exchange_rate ?? "1")
                    );
                    setNotes(editPayment.notes ?? "");
                } else {
                    setAmount(String(prefillAmount ?? balanceDue));
                    setPaymentDate(new Date().toISOString().split("T")[0]);
                    setReference("");
                    setInvoiceNumber("");
                    setPaymentCurrency(poCurrency);
                    setExchangeRate("1");
                    setNotes("");
                }
                setPaymentMethod("bank_transfer");
                setInvoiceFiles([]);
                setPopFiles([]);
                setDeletedDocIds(new Set());
                setError(null);
            })
        }
    }, [isOpen, prefillAmount, balanceDue, editPayment, poCurrency]);

    // Keep the rate pinned to 1 whenever the payment currency matches the PO
    // currency (the backend enforces this too).
    useEffect(() => {
        if (isPurchaseOrder && paymentCurrency === poCurrency) {
            setExchangeRate("1");
        }
    }, [isPurchaseOrder, paymentCurrency, poCurrency]);

    const handleFilesPicked =
        (setter: React.Dispatch<React.SetStateAction<File[]>>, ref: React.RefObject<HTMLInputElement | null>) =>
            (e: React.ChangeEvent<HTMLInputElement>) => {
                const picked = Array.from(e.target.files ?? []);
                if (picked.length) setter((prev) => [...prev, ...picked]);
                if (ref.current) ref.current.value = "";
            };

    const removeFile = (
        setter: React.Dispatch<React.SetStateAction<File[]>>,
        index: number
    ) => setter((prev) => prev.filter((_, i) => i !== index));

    const markExistingForDeletion = (docId: string) => {
        setDeletedDocIds((prev) => new Set(prev).add(docId));
    };

    // Converted PO-currency amount preview (for a cross-currency payment).
    const convertedAmount = (() => {
        const a = parseFloat(amount);
        const r = parseFloat(exchangeRate);
        if (isNaN(a) || isNaN(r)) return null;
        return Math.round((a * r + Number.EPSILON) * 100) / 100;
    })();

    const handleRecord = async () => {
        setError(null);
        const parsedAmount = parseFloat(amount);
        if (isNaN(parsedAmount) || parsedAmount <= 0) {
            setError("Amount must be greater than 0");
            return;
        }

        // Reference is required for purchase-order payments.
        if (isPurchaseOrder && !reference.trim()) {
            setError("Payment reference is required");
            return;
        }

        const parsedRate = parseFloat(exchangeRate);
        if (isPurchaseOrder) {
            if (isNaN(parsedRate) || parsedRate <= 0) {
                setError("Exchange rate must be greater than 0");
                return;
            }
            if (paymentCurrency === poCurrency && parsedRate !== 1) {
                setError("Exchange rate must be 1 when the payment currency matches the PO currency");
                return;
            }
        }

        setIsSubmitting(true);
        try {
            // Edit mode (purchase orders only): update the existing payment,
            // delete removed documents, upload new invoice + POP files.
            if (isEditing && isPurchaseOrder) {
                await updatePurchaseOrderPayment(entityId, editPayment.id, {
                    amount: parsedAmount,
                    paymentDate,
                    reference: reference.trim(),
                    invoiceNumber: invoiceNumber.trim() || undefined,
                    currency: paymentCurrency,
                    exchangeRate: parsedRate,
                    notes: notes || undefined,
                });
                for (const docId of deletedDocIds) {
                    await deletePurchaseOrderDocument(entityId, docId);
                }
                for (const f of invoiceFiles) {
                    await uploadPurchaseOrderDocument(entityId, f, "payment_modal", editPayment.id, "invoice");
                }
                for (const f of popFiles) {
                    await uploadPurchaseOrderDocument(entityId, f, "payment_modal", editPayment.id, "pop");
                }
                onSuccess();
                return;
            }
            if (entityType === "invoice") {
                const payload: InvoicePaymentPayload = {
                    amount: parsedAmount,
                    paymentDate,
                    paymentMethod,
                    reference: reference || undefined,
                    notes: notes || undefined,
                };
                await recordInvoicePayment(entityId, payload);
            } else if (entityType === "expense") {
                const payload: ExpensePaymentPayload = {
                    amount: parsedAmount,
                    paymentDate,
                    reference: reference || undefined,
                    notes: notes || undefined,
                };
                await recordExpensePayment(entityId, payload);
            } else if (isPurchaseOrder) {
                // Record the payment first to obtain its id, then upload each
                // selected invoice / POP document grouped under that payment
                // (source payment_modal + paymentId + documentType) so the
                // payment carries the full set. Resolved on view via payment_id.
                const payload: PurchaseOrderPaymentPayload = {
                    amount: parsedAmount,
                    paymentDate,
                    reference: reference.trim(),
                    invoiceNumber: invoiceNumber.trim() || undefined,
                    currency: paymentCurrency,
                    exchangeRate: parsedRate,
                    notes: notes || undefined,
                };
                const payment = await recordPurchaseOrderPayment(entityId, payload);
                for (const f of invoiceFiles) {
                    await uploadPurchaseOrderDocument(entityId, f, "payment_modal", payment.id, "invoice");
                }
                for (const f of popFiles) {
                    await uploadPurchaseOrderDocument(entityId, f, "payment_modal", payment.id, "pop");
                }
            }
            onSuccess();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to record payment");
        } finally {
            setIsSubmitting(false);
        }
    };

    const renderUploader = (
        title: string,
        docType: "invoice" | "pop",
        files: File[],
        setter: React.Dispatch<React.SetStateAction<File[]>>,
        ref: React.RefObject<HTMLInputElement | null>,
        existing: PurchaseOrderDocument[],
    ) => (
        <div className="space-y-2">
            <Label className="font-bold text-base">{title}</Label>

            {/* Existing documents (edit mode) with a delete button. */}
            {existing.length > 0 && (
                <ul className="flex flex-col gap-2">
                    {existing.map((doc) => (
                        <li
                            key={doc.id}
                            className="flex items-center justify-between gap-3 px-3 py-3 bg-gray-50 border border-gray-200 rounded-lg"
                        >
                            <span className="flex items-center gap-2 min-w-0 text-sm text-gray-700">
                                <Paperclip size={16} className="shrink-0 text-gray-500" />
                                <span className="truncate" title={doc.filename}>{doc.filename}</span>
                            </span>
                            <button
                                type="button"
                                onClick={() => markExistingForDeletion(doc.id)}
                                className="flex items-center gap-1.5 text-sm text-red-500 hover:text-red-700 transition-colors"
                                aria-label={`Remove ${doc.filename}`}
                            >
                                <Trash2 size={16} /> Remove
                            </button>
                        </li>
                    ))}
                </ul>
            )}

            <Button
                type="button"
                variant="outline"
                onClick={() => ref.current?.click()}
                className="flex items-center gap-2 w-full border-dashed h-20 text-[16px]"
            >
                <Plus size={18} /> {title}
            </Button>
            <input
                ref={ref}
                type="file"
                multiple
                className="hidden"
                accept={ACCEPTED_UPLOAD_TYPES}
                onChange={handleFilesPicked(setter, ref)}
                aria-label={title}
            />
            {files.length > 0 && (
                <ul className="mt-2 flex flex-col gap-2">
                    {files.map((file, index) => (
                        <li
                            key={`${docType}-${file.name}-${index}`}
                            className="flex items-center justify-between gap-3 px-3 py-3 bg-white border border-gray-200 rounded-lg"
                        >
                            <span className="flex items-center gap-2 min-w-0 text-sm text-gray-700">
                                <Paperclip size={16} className="shrink-0 text-gray-500" />
                                <span className="truncate" title={file.name}>{file.name}</span>
                            </span>
                            <button
                                type="button"
                                onClick={() => removeFile(setter, index)}
                                className="text-gray-400 hover:text-red-500 transition-colors"
                                aria-label={`Remove ${file.name}`}
                            >
                                <X size={18} />
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );

    return (
        <Dialog
            isOpen={isOpen}
            onClose={onClose}
            title={isEditing ? "Edit Payment" : "Record Payment"}
            icon={<CreditCard size={24} />}
            confirmLabel={isEditing ? "Save changes" : "Save Payment"}
            cancelLabel="Cancel"
            onConfirm={handleRecord}
            isLoading={isSubmitting}
        >
            <div className="space-y-6 bg-white p-6 rounded-xl border border-gray-200">
                {error && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
                )}

                <div className="space-y-2">
                    <Label htmlFor="payment-amount" className="font-bold text-base">Amount</Label>
                    <Input
                        id="payment-amount"
                        type="number"
                        step="0.01"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        placeholder="Enter amount"
                    />
                </div>

                <div className="space-y-2">
                    <Label htmlFor="payment-date" className="font-bold text-base">Date</Label>
                    <Input
                        id="payment-date"
                        type="date"
                        value={paymentDate}
                        onChange={(e) => setPaymentDate(e.target.value)}
                    />
                </div>

                {/* Payment currency + exchange rate (purchase orders only). */}
                {isPurchaseOrder && (
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label htmlFor="payment-currency" className="font-bold text-base">Payment Currency</Label>
                            <Select
                                id="payment-currency"
                                value={paymentCurrency}
                                onChange={(e) => setPaymentCurrency(e.target.value)}
                                options={CURRENCY_OPTIONS.map((c) => ({ value: c.value, label: c.label }))}
                            />
                        </div>
                        {currencyDiffers && (
                            <div className="space-y-2">
                                <Label htmlFor="exchange-rate" className="font-bold text-base">
                                    Exchange Rate (1 {paymentCurrency} → {poCurrency})
                                </Label>
                                <Input
                                    id="exchange-rate"
                                    type="number"
                                    step="0.00000001"
                                    value={exchangeRate}
                                    onChange={(e) => setExchangeRate(e.target.value)}
                                    placeholder="e.g. 129.5"
                                />
                            </div>
                        )}
                    </div>
                )}
                {currencyDiffers && convertedAmount !== null && (
                    <p className="text-sm text-gray-500">
                        Applied to balance: <span className="font-semibold text-gray-700">{formatCurrency(convertedAmount, poCurrency)}</span>
                    </p>
                )}

                {/* payment_method: invoice payments only. */}
                {entityType === "invoice" && (
                    <div className="space-y-2">
                        <Label htmlFor="payment-method" className="font-bold text-base">Payment Method</Label>
                        <Select
                            id="payment-method"
                            value={paymentMethod}
                            onChange={(e) => setPaymentMethod(e.target.value)}
                            options={PAYMENT_METHODS}
                        />
                    </div>
                )}

                <div className="space-y-2">
                    <Label htmlFor="payment-reference" className="font-bold text-base">
                        {isPurchaseOrder ? "Payment Ref #" : "Reference (optional)"}
                    </Label>
                    <Input
                        id="payment-reference"
                        type="text"
                        value={reference}
                        onChange={(e) => setReference(e.target.value)}
                        placeholder="Transaction ID, cheque number..."
                    />
                </div>

                {/* Invoice number: purchase orders only (free-text, optional). */}
                {isPurchaseOrder && (
                    <div className="space-y-2">
                        <Label htmlFor="payment-invoice" className="font-bold text-base">Invoice # (optional)</Label>
                        <Input
                            id="payment-invoice"
                            type="text"
                            value={invoiceNumber}
                            onChange={(e) => setInvoiceNumber(e.target.value)}
                            placeholder="Bill/invoice reference(s) this payment covers"
                        />
                    </div>
                )}

                <div className="space-y-2">
                    <Label htmlFor="payment-notes" className="font-bold text-base">Notes (optional)</Label>
                    <Input
                        id="payment-notes"
                        type="text"
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                        placeholder="Additional notes..."
                    />
                </div>

                {/* Two independent uploaders (purchase orders only). */}
                {supportsAttachments && (
                    <div className="space-y-4">
                        {renderUploader("Upload Invoice", "invoice", invoiceFiles, setInvoiceFiles, invoiceInputRef, relatedInvoiceDocs)}
                        {renderUploader("Upload POP", "pop", popFiles, setPopFiles, popInputRef, relatedPopDocs)}
                    </div>
                )}
            </div>
        </Dialog>
    );
}
