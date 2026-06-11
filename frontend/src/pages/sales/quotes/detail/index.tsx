import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { SendQuoteModal } from "@/components/modals/SendQuoteModal";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { saveBlob } from "@/lib/utils";
import {
    approveQuote,
    convertQuoteToInvoice,
    deleteQuote,
    downloadQuotePdf,
    duplicateQuote,
    getQuote,
    markQuoteAsSent,
    type QuoteResponse,
} from "@/services/quoteApi";
import {
    ArrowLeft,
    CheckCircle,
    Copy,
    Download,
    FileText,
    Pencil,
    Send,
    Trash
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

export default function QuoteDetailPage() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const [quote, setQuote] = useState<QuoteResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showSendModal, setShowSendModal] = useState(false);

    const fetchQuote = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        try {
            const data = await getQuote(id);
            setQuote(data);
        } catch (err) {
            console.error("[QuoteDetail] Failed:", err);
            setError(err instanceof Error ? err.message : "Failed to load quote");
        } finally {
            setIsLoading(false);
        }
    }, [id]);

    useEffect(() => {
        void (async () => { await fetchQuote(); })();
    }, [fetchQuote]);

    const handleMarkSent = async () => {
        if (!quote) return;
        try {
            await markQuoteAsSent(quote.id);
            fetchQuote();
        } catch (err) {
            console.error("[QuoteDetail] Mark sent failed:", err);
        }
    };

    const handleDuplicate = async () => {
        if (!quote) return;
        try {
            const dup = await duplicateQuote(quote.id);
            navigate(`/quotes/${dup.new_quote_id}/edit`);
        } catch (err) {
            console.error("[QuoteDetail] Duplicate failed:", err);
            setError(
                err instanceof Error ? err.message : "Failed to duplicate quote"
            );
        }
    };

    const handleDelete = async () => {
        if (!quote) return;
        if (!confirm(`Delete quote ${quote.quote_reference}? This is irreversible.`)) return;
        try {
            await deleteQuote(quote.id);
            navigate("/quotes");
        } catch (err) {
            console.error("[QuoteDetail] Delete failed:", err);
        }
    };

    const handleApprove = async () => {
        if (!quote) return;
        try {
            await approveQuote(quote.id);
            fetchQuote();
        } catch (err) {
            console.error("[QuoteDetail] Approve failed:", err);
        }
    };

    const handleDownloadPdf = async () => {
        if (!quote) return;
        try {
            const blob = await downloadQuotePdf(quote.id);
            saveBlob(blob, `Quote_${quote.quote_reference}.pdf`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to download PDF");
        }
    };

    const handleConvertToInvoice = async () => {
        if (!quote) return;
        try {
            const res = await convertQuoteToInvoice(quote.id);
            navigate(`/invoices/${res.invoice_id}`);
        } catch (err) {
            console.error("[QuoteDetail] Convert to invoice failed:", err);
            alert("Failed to convert to invoice.");
        }
    };

    if (isLoading) {
        return <LoadingState message="Loading quote..." />;
    }

    if (error || !quote) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-2">
                <p className="text-red-500">{error || "Quote not found"}</p>
                <Button onClick={() => navigate("/quotes")} className="text-priori-purple">Back to Quotes</Button>
            </div>
        );
    }

    const status = quote.status.toLowerCase();

    // Determine badge variant
    let badgeVariant: "draft" | "expired" | "invoiced" | "approved" | "sent" = "draft";
    if (quote.is_expired) {
        badgeVariant = "expired";
    } else if (status === "invoiced") {
        badgeVariant = "invoiced";
    } else if (status === "approved") {
        badgeVariant = "approved";
    } else if (status === "sent") {
        badgeVariant = "sent";
    }

    // Build actions
    const actions = [];
    if (quote.is_editable) {
        actions.push({ key: "edit", label: "Edit", icon: <Pencil size={16} />, onClick: () => navigate(`/quotes/${quote.id}/edit`) });
    }
    if (status === "draft") {
        actions.push({ key: "mark-sent", label: "Mark as Sent", icon: <CheckCircle size={16} />, onClick: handleMarkSent });
    }
    actions.push({ key: "send", label: "Send", icon: <Send size={16} />, onClick: () => setShowSendModal(true) });

    if (["draft", "sent"].includes(status) && !quote.is_expired) {
        actions.push({ key: "approve", label: "Approve", icon: <CheckCircle size={16} />, onClick: handleApprove });
    }

    if (quote.can_convert_to_invoice) {
        actions.push({ key: "convert", label: "Convert to Invoice", icon: <FileText size={16} />, onClick: handleConvertToInvoice });
    }

    actions.push({ key: "pdf", label: "Download PDF", icon: <Download size={16} />, onClick: handleDownloadPdf });
    actions.push({ key: "duplicate", label: "Duplicate", icon: <Copy size={16} />, onClick: handleDuplicate });

    if (status === "draft") {
        actions.push({ key: "delete", label: "Delete Quote", icon: <Trash size={16} />, onClick: handleDelete });
    }

    return (
        <div className="flex flex-col gap-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <button type="button" onClick={() => navigate("/quotes")} className="text-gray-500 hover:text-gray-700">
                        <ArrowLeft size={20} />
                    </button>
                    <h2 className="text-2xl font-bold text-gray-800">{quote.quote_reference}</h2>
                    <Badge variant={badgeVariant}>
                        {quote.is_expired ? `Expired` : status.charAt(0).toUpperCase() + status.slice(1)}
                    </Badge>
                </div>
                <Dropdown items={actions} className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors" />
            </div>

            <DocumentViewer
                type="quote"
                data={{
                    documentReference: quote.quote_reference,
                    customerId: quote.customer_id,
                    customer: quote.customer,
                    transactionDate: quote.transaction_date,
                    dueDate: quote.due_date,
                    currency: quote.currency,
                    rfqNumber: quote.rfq_rfp_number || undefined,
                    notes: quote.notes || undefined,
                    discountType: (quote.discount_type === "amount" || quote.discount_type === "percentage")
                        ? quote.discount_type
                        : null,
                    discountAmount: quote.discount_amount == null ? null : Number(quote.discount_amount),
                    discountPercentage: quote.discount_percentage == null ? null : Number(quote.discount_percentage),
                    subtotal: Number(quote.subtotal),
                    taxTotal: Number(quote.tax_total),
                    totalDue: Number(quote.total_due),
                    lineItems: (quote.line_items ?? []).map(item => ({
                        id: item.id,
                        itemName: item.item_name || item.description.split('\n')[0] || "",
                        description: item.description,
                        quantity: Number(item.quantity),
                        unitPrice: Number(item.unit_price),
                        taxType: item.tax_type,
                        lineTotal: Number(item.line_total),
                    }))
                }}
            />

            {/* Bottom CTAs */}
            <div className="flex justify-end gap-3">
                {quote.can_convert_to_invoice && (
                    <Button
                        onClick={handleConvertToInvoice}
                        className="bg-green-600 hover:bg-green-700 text-white rounded-lg px-6 py-3"
                    >
                        <FileText size={16} className="mr-2" />
                        Convert to Invoice
                    </Button>
                )}
                {quote.is_editable && (
                    <Button
                        onClick={() => navigate(`/quotes/${quote.id}/edit`)}
                        className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg px-6 py-3"
                    >
                        <Pencil size={16} className="mr-2" />
                        Edit Quote
                    </Button>
                )}
            </div>

            {/* Send Quote Modal */}
            <SendQuoteModal
                isOpen={showSendModal}
                onClose={() => setShowSendModal(false)}
                quote={quote}
                onSuccess={() => {
                    setShowSendModal(false);
                    fetchQuote();
                }}
            />
        </div>
    );
}
