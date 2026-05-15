import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import type { InvoiceResponse } from "@/lib/invoiceApi";
import { Send } from "lucide-react";
import { useState } from "react";

interface SendInvoiceModalProps {
    isOpen: boolean;
    onClose: () => void;
    invoice: InvoiceResponse;
    onSuccess: () => void;
}

export function SendInvoiceModal({ isOpen, onClose, invoice, onSuccess }: SendInvoiceModalProps) {
    const [toEmail, setToEmail] = useState("");
    const [subject, setSubject] = useState(`Invoice ${invoice.invoice_reference} from Priori Technologies`);
    const [body, setBody] = useState(
        `Dear Customer,\n\nPlease find attached Invoice ${invoice.invoice_reference} for the amount of ${invoice.total_due}.\n\nThank you for your business.\n\nPriori Technologies`
    );

    const handleSend = () => {
        console.log("[SendInvoice] Sending:", { toEmail, subject, body });
        alert("Email sending is not yet implemented. Coming soon!");
        onSuccess();
    };

    return (
        <Dialog
            isOpen={isOpen}
            onClose={onClose}
            title="Send Invoice"
            icon={<Send size={24} />}
            confirmLabel="Send"
            cancelLabel="Cancel"
            onConfirm={handleSend}
        >
            <div className="space-y-4">
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700">
                    Email sending is not yet implemented. This is a preview of the interface.
                </div>
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
