import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { formatCurrency } from "@/lib/utils";
import { recordPayment as recordExpensePayment, type ExpensePaymentPayload } from "@/services/expenseApi";
import { recordPayment as recordInvoicePayment, type PaymentCreatePayload as InvoicePaymentPayload } from "@/services/invoiceApi";
import { recordPurchaseOrderPayment, type PurchaseOrderPaymentPayload } from "@/services/purchaseOrderApi";
import { CreditCard } from "lucide-react";
import { startTransition, useEffect, useState } from "react";

interface RecordPaymentModalProps {
    isOpen: boolean;
    onClose: () => void;
    entityId: string;
    entityType: "invoice" | "expense" | "purchaseOrder";
    balanceDue: number;
    currency: string;
    prefillAmount?: number;
    reference?: string; // e.g. Invoice Ref or Expense Ref for display
    onSuccess: () => void;
}

const ENTITY_TYPE_LABELS: Record<RecordPaymentModalProps["entityType"], string> = {
    invoice: "Invoice",
    expense: "Expense",
    purchaseOrder: "Purchase Order",
};

const PAYMENT_METHODS = [
    { value: "cash", label: "Cash" },
    { value: "bank_transfer", label: "Bank Transfer" },
    { value: "check", label: "Check" },
    { value: "card", label: "Card" },
    { value: "mobile_money", label: "Mobile Money" },
    { value: "other", label: "Other" },
];

export function RecordPaymentModal({ isOpen, onClose, entityId, entityType, balanceDue, currency, prefillAmount, reference: displayRef, onSuccess }: RecordPaymentModalProps) {
    const [amount, setAmount] = useState(String(prefillAmount ?? balanceDue));
    const [paymentDate, setPaymentDate] = useState(new Date().toISOString().split("T")[0]);
    const [paymentMethod, setPaymentMethod] = useState("bank_transfer");
    const [reference, setReference] = useState("");
    const [notes, setNotes] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (isOpen) {
            startTransition(() => {
                setAmount(String(prefillAmount ?? balanceDue));
                setPaymentDate(new Date().toISOString().split("T")[0]);
                setPaymentMethod("bank_transfer");
                setReference("");
                setNotes("");
                setError(null);
            })
        }
    }, [isOpen, prefillAmount, balanceDue]);

    const handleRecord = async () => {
        setError(null);
        const parsedAmount = parseFloat(amount);
        if (isNaN(parsedAmount) || parsedAmount <= 0) {
            setError("Amount must be greater than 0");
            return;
        }
        if (parsedAmount > balanceDue) {
            setError(`Amount cannot exceed balance due (${formatCurrency(balanceDue, currency)})`);
            return;
        }

        setIsSubmitting(true);
        try {
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
                const payload: PurchaseOrderPaymentPayload = {
                    amount: parsedAmount,
                    paymentDate,
                    reference: reference || undefined,
                    notes: notes || undefined,
                };
                await recordPurchaseOrderPayment(entityId, payload);
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
            title="Record Payment"
            icon={<CreditCard size={24} />}
            confirmLabel="Record"
            cancelLabel="Cancel"
            onConfirm={handleRecord}
            isLoading={isSubmitting}
        >
            <div className="space-y-4">
                <div className="flex justify-between text-sm p-3 bg-gray-50 rounded-lg">
                    <span className="text-gray-500">{ENTITY_TYPE_LABELS[entityType]}: {displayRef || entityId.slice(0, 8)}</span>
                    <span className="font-medium text-gray-800">Balance: {formatCurrency(balanceDue, currency)}</span>
                </div>

                {error && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
                )}

                <div>
                    <Label htmlFor="payment-amount">Amount</Label>
                    <Input
                        id="payment-amount"
                        type="number"
                        step="0.01"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        placeholder="0.00"
                    />
                </div>

                <div>
                    <Label htmlFor="payment-date">Date</Label>
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
                    <div>
                        <Label htmlFor="payment-method">Payment Method</Label>
                        <Select
                            id="payment-method"
                            value={paymentMethod}
                            onChange={(e) => setPaymentMethod(e.target.value)}
                            options={PAYMENT_METHODS}
                        />
                    </div>
                )}

                <div>
                    <Label htmlFor="payment-reference">Reference (optional)</Label>
                    <Input
                        id="payment-reference"
                        type="text"
                        value={reference}
                        onChange={(e) => setReference(e.target.value)}
                        placeholder="Transaction ID, check number..."
                    />
                </div>

                <div>
                    <Label htmlFor="payment-notes">Notes (optional)</Label>
                    <Input
                        id="payment-notes"
                        type="text"
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                        placeholder="Additional notes..."
                    />
                </div>
            </div>
        </Dialog>
    );
}
