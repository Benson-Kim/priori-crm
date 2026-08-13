import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { sendQuote, type QuoteResponse } from "@/services/quoteApi";
import { Send } from "lucide-react";
import { useState } from "react";

interface SendQuoteModalProps {
    isOpen: boolean;
    onClose: () => void;
    quote: QuoteResponse;
    onSuccess: () => void;
}

export function SendQuoteModal({ isOpen, onClose, quote, onSuccess }: SendQuoteModalProps) {
    const [toEmail, setToEmail] = useState(quote.customer?.email ?? "");
    const [subject, setSubject] = useState(
        `Quote ${quote.quote_reference} from Business Central`
    );
    const [body, setBody] = useState(
        `Dear Customer,\n\nPlease find attached Quote ${quote.quote_reference} for the amount of ${quote.total_due}.\n\nThank you for considering our proposal.\n\nBusiness Central`
    );
    const [isSending, setIsSending] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleSend = async () => {
        setError(null);
        setIsSending(true);
        try {
            await sendQuote(quote.id, {
                toEmail: toEmail || undefined,
                subject: subject || undefined,
                body: body || undefined,
            });
            onSuccess();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to send quote");
        } finally {
            setIsSending(false);
        }
    };

    return (
        <Dialog
            isOpen={isOpen}
            onClose={onClose}
            title="Send Quote"
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
                    <Label htmlFor="send-quote-to">To</Label>
                    <Input
                        id="send-quote-to"
                        type="email"
                        value={toEmail}
                        onChange={(e) => setToEmail(e.target.value)}
                        placeholder="customer@example.com"
                    />
                </div>
                <div>
                    <Label htmlFor="send-quote-subject">Subject</Label>
                    <Input
                        id="send-quote-subject"
                        type="text"
                        value={subject}
                        onChange={(e) => setSubject(e.target.value)}
                    />
                </div>
                <div>
                    <Label htmlFor="send-quote-body">Body</Label>
                    <textarea
                        id="send-quote-body"
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
