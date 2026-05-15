import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { recordPayment, type InvoiceResponse, type PaymentCreatePayload } from "@/lib/invoiceApi";
import { formatCurrency } from "@/lib/utils";
import { CreditCard } from "lucide-react";
import { useState } from "react";

interface RecordPaymentModalProps {
    isOpen: boolean;
    onClose: () => void;
    invoice: InvoiceResponse;
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

export function RecordPaymentModal({ isOpen, onClose, invoice, onSuccess }: RecordPaymentModalProps) {
    const [amount, setAmount] = useState(String(invoice.balance_due));
    const [paymentDate, setPaymentDate] = useState(new Date().toISOString().split("T")[0]);
    const [paymentMethod, setPaymentMethod] = useState("bank_transfer");
    const [reference, setReference] = useState("");
    const [notes, setNotes] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleRecord = async () => {
        setError(null);
        const parsedAmount = parseFloat(amount);
        if (isNaN(parsedAmount) || parsedAmount <= 0) {
            setError("Amount must be greater than 0");
            return;
        }
        if (parsedAmount > invoice.balance_due) {
            setError(`Amount cannot exceed balance due (${formatCurrency(invoice.balance_due, invoice.currency)})`);
            return;
        }

        setIsSubmitting(true);
        try {
            const payload: PaymentCreatePayload = {
                amount: parsedAmount,
                paymentDate,
                paymentMethod,
                reference: reference || undefined,
                notes: notes || undefined,
            };
            const result = await recordPayment(invoice.id, payload);
            console.log("[RecordPayment] Success:", result);
            onSuccess();
        } catch (err) {
            console.error("[RecordPayment] Failed:", err);
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
                    <span className="text-gray-500">Invoice: {invoice.invoice_reference}</span>
                    <span className="font-medium text-gray-800">Balance: {formatCurrency(invoice.balance_due, invoice.currency)}</span>
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

                <div>
                    <Label htmlFor="payment-method">Payment Method</Label>
                    <Select
                        id="payment-method"
                        value={paymentMethod}
                        onChange={(e) => setPaymentMethod(e.target.value)}
                        options={PAYMENT_METHODS}
                    />
                </div>

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
