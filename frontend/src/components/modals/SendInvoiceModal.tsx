import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { sendInvoice, type InvoiceResponse } from "@/services/invoiceApi";
import { Send } from "lucide-react";
import { useState } from "react";

interface SendInvoiceModalProps {
    isOpen: boolean;
    onClose: () => void;
    invoice: InvoiceResponse;
    onSuccess: () => void;
}

export function SendInvoiceModal({ isOpen, onClose, invoice, onSuccess }: SendInvoiceModalProps) {
    const [toEmail, setToEmail] = useState(invoice.customer?.email ?? "");
    const [subject, setSubject] = useState(`Invoice ${invoice.invoice_reference} from Priori Technologies`);
    const [body, setBody] = useState(
        `Dear Customer,\n\nPlease find attached Invoice ${invoice.invoice_reference} for the amount of ${invoice.total_due}.\n\nThank you for your business.\n\nPriori Technologies`
    );
    const [isSending, setIsSending] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleSend = async () => {
        setError(null);
        setIsSending(true);
        try {
            await sendInvoice(invoice.id, {
                toEmail: toEmail || undefined,
                subject: subject || undefined,
                body: body || undefined,
            });
            onSuccess();
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to send invoice"
            );
        } finally {
            setIsSending(false);
        }
    };

    return (
        <Dialog
            isOpen={isOpen}
            onClose={onClose}
            title="Send Invoice"
            icon={<Send size={24} />}
            confirmLabel={isSending ? "Sending..." : "Send"}
            cancelLabel="Cancel"
            onConfirm={handleSend}
        >
            <div className="space-y-4">
                {error && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                        {error}
                    </div>
                )}
                <div>
                    <Label htmlFor="send-to">To</Label>
                    <Input
                        id="send-to"
                        type="email"
                        value={toEmail}
                        onChange={(e) => setToEmail(e.target.value)}
                        placeholder="customer@example.com"
                    />
                </div>
                <div>
                    <Label htmlFor="send-subject">Subject</Label>
                    <Input
                        id="send-subject"
                        type="text"
                        value={subject}
                        onChange={(e) => setSubject(e.target.value)}
                    />
                </div>
                <div>
                    <Label htmlFor="send-body">Body</Label>
                    <textarea
                        id="send-body"
                        value={body}
                        onChange={(e) => setBody(e.target.value)}
                        rows={5}
                        className="w-full p-3 border border-gray-300 rounded-lg bg-gray-50 text-sm text-gray-800 focus:outline-none focus:border-priori-purple resize-none"
                    />
                </div>
            </div>
        </Dialog>
    );
}
