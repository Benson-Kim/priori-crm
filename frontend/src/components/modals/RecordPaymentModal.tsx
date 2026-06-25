import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { ACCEPTED_UPLOAD_TYPES } from "@/lib/constants";
import { formatCurrency } from "@/lib/utils";
import { recordPayment as recordExpensePayment, type ExpensePaymentPayload } from "@/services/expenseApi";
import { recordPayment as recordInvoicePayment, type PaymentCreatePayload as InvoicePaymentPayload } from "@/services/invoiceApi";
import { recordPurchaseOrderPayment, updatePurchaseOrderPayment, uploadPurchaseOrderDocument, type PurchaseOrderPayment, type PurchaseOrderPaymentPayload } from "@/services/purchaseOrderApi";
import { CreditCard, Paperclip, Plus, X } from "lucide-react";
import { startTransition, useEffect, useRef, useState } from "react";

interface RecordPaymentModalProps {
    isOpen: boolean;
    onClose: () => void;
    entityId: string;
    entityType: "invoice" | "expense" | "purchaseOrder";
    balanceDue: number;
    currency: string;
    prefillAmount?: number;
    reference?: string; // e.g. Invoice Ref or Expense Ref for display
    /**
     * When supplied, the modal edits this existing payment (purchase orders
     * only) instead of recording a new one: fields prefill from it and Save
     * calls updatePurchaseOrderPayment. The editable balance is the live
     * balanceDue plus this payment's own amount (removing it frees that).
     */
    editPayment?: PurchaseOrderPayment | null;
    onSuccess: () => void;
}

// const ENTITY_TYPE_LABELS: Record<RecordPaymentModalProps["entityType"], string> = {
//     invoice: "Invoice",
//     expense: "Expense",
//     purchaseOrder: "Purchase Order",
// };

const PAYMENT_METHODS = [
    { value: "cash", label: "Cash" },
    { value: "bank_transfer", label: "Bank Transfer" },
    { value: "check", label: "Check" },
    { value: "card", label: "Card" },
    { value: "mobile_money", label: "Mobile Money" },
    { value: "other", label: "Other" },
];

export function RecordPaymentModal({
    isOpen,
    onClose,
    entityId,
    entityType,
    balanceDue,
    currency,
    prefillAmount,
    // reference: displayRef, 
    editPayment,
    onSuccess
}: RecordPaymentModalProps) {
    const isEditing = !!editPayment;
    // In edit mode the amount can grow back into the balance this payment
    // already occupies, so the effective cap is balanceDue + its own amount.
    const effectiveBalance = isEditing
        ? balanceDue + Number(editPayment.amount)
        : balanceDue;
    const [amount, setAmount] = useState(String(prefillAmount ?? balanceDue));
    const [paymentDate, setPaymentDate] = useState(new Date().toISOString().split("T")[0]);
    const [paymentMethod, setPaymentMethod] = useState("bank_transfer");
    const [reference, setReference] = useState("");
    const [notes, setNotes] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // Proof-of-payment attachments (purchase orders only). On submit the
    // payment is recorded first, then every selected file is uploaded with
    // source `payment_modal` and grouped under that payment (paymentId) so the
    // payment carries the full set.
    const [files, setFiles] = useState<File[]>([]);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Only the purchase-order payment API supports linking a proof-of-payment
    // document, so the attach control is shown for that entity type only.
    // New PO payments can attach proof-of-payment files; the edit flow updates
    // the payment record only (documents are managed from the view modal).
    const supportsAttachments = entityType === "purchaseOrder" && !isEditing;

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
                    setNotes(editPayment.notes ?? "");
                } else {
                    setAmount(String(prefillAmount ?? balanceDue));
                    setPaymentDate(new Date().toISOString().split("T")[0]);
                    setReference("");
                    setNotes("");
                }
                setPaymentMethod("bank_transfer");
                setFiles([]);
                setError(null);
            })
        }
    }, [isOpen, prefillAmount, balanceDue, editPayment]);

    const handleFilesPicked = (e: React.ChangeEvent<HTMLInputElement>) => {
        const picked = Array.from(e.target.files ?? []);
        if (picked.length) {
            setFiles((prev) => [...prev, ...picked]);
        }
        if (fileInputRef.current) fileInputRef.current.value = "";
    };

    const removeFile = (index: number) =>
        setFiles((prev) => prev.filter((_, i) => i !== index));

    const handleRecord = async () => {
        setError(null);
        const parsedAmount = parseFloat(amount);
        if (isNaN(parsedAmount) || parsedAmount <= 0) {
            setError("Amount must be greater than 0");
            return;
        }
        if (parsedAmount > effectiveBalance) {
            setError(`Amount cannot exceed balance due (${formatCurrency(effectiveBalance, currency)})`);
            return;
        }

        setIsSubmitting(true);
        try {
            // Edit mode (purchase orders only): update the existing payment
            // and re-derive the PO balance / status server-side.
            if (isEditing && entityType === "purchaseOrder") {
                await updatePurchaseOrderPayment(entityId, editPayment.id, {
                    amount: parsedAmount,
                    paymentDate,
                    reference: reference || undefined,
                    notes: notes || undefined,
                });
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
            } else if (entityType === "purchaseOrder") {
                // Record the payment first to obtain its id, then upload every
                // selected proof-of-payment document grouped under that payment
                // (source payment_modal + paymentId) so the payment carries the
                // full set. Documents are resolved on view via payment_id.
                const payload: PurchaseOrderPaymentPayload = {
                    amount: parsedAmount,
                    paymentDate,
                    reference: reference || undefined,
                    notes: notes || undefined,
                };
                const payment = await recordPurchaseOrderPayment(entityId, payload);
                for (const f of files) {
                    await uploadPurchaseOrderDocument(
                        entityId,
                        f,
                        "payment_modal",
                        payment.id
                    );
                }
            }
            console.log(`[RecordPayment] Success for ${entityType}`);
            onSuccess();
        } catch (err) {
            console.error(`[RecordPayment] Failed for ${entityType}:`, err);
            setError(err instanceof Error ? err.message : "Failed to record payment");
        } finally {
            setIsSubmitting(false);
        }
    };

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

                {/* Only the invoice payment API carries a payment_method field; the
                    expense and purchase-order payment APIs do not, so the selection
                    would be silently dropped — only show it for invoices. */}
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
                    <Label htmlFor="payment-reference" className="font-bold text-base">Reference (optional)</Label>
                    <Input
                        id="payment-reference"
                        type="text"
                        value={reference}
                        onChange={(e) => setReference(e.target.value)}
                        placeholder="Transaction ID, check number..."
                    />
                </div>

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

                {/* Proof-of-payment attachments (purchase orders only). */}
                {supportsAttachments && (
                    <div className="space-y-2">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => fileInputRef.current?.click()}
                            className="flex items-center gap-2 w-full border-dashed h-28 text-[20px]"
                        >
                            <Plus size={20} /> Upload document
                        </Button>
                        <input
                            id="payment-documents"
                            ref={fileInputRef}
                            type="file"
                            multiple
                            className="hidden"
                            accept={ACCEPTED_UPLOAD_TYPES}
                            onChange={handleFilesPicked}
                        />
                        {files.length > 0 && (
                            <ul className="mt-2 flex flex-col gap-2">
                                {files.map((file, index) => (
                                    <li
                                        key={`${file.name}-${index}`}
                                        className="flex items-center justify-between gap-3 px-3 py-4 bg-white border border-gray-200 rounded-lg"
                                    >
                                        <span className="flex items-center gap-2 min-w-0 text-sm text-gray-700">
                                            <Paperclip size={16} className="shrink-0 text-gray-500" />
                                            <span className="truncate" title={file.name}>{file.name}</span>
                                        </span>
                                        <button
                                            type="button"
                                            onClick={() => removeFile(index)}
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
                )}
            </div>
        </Dialog>
    );
}
