import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { RecordPaymentModal } from "@/components/invoices/RecordPaymentModal";
import { useHeaderOverride } from "@/components/layout/default-layout";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import type { InvoiceResponse } from "@/lib/invoiceApi";
import { cancelInvoice, duplicateInvoice, getInvoice, markAsSent } from "@/lib/invoiceApi";
import { ArrowLeft, Ban, CheckCircle, Copy, CreditCard, Download, Pencil, Send } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

export function InvoiceDetailPage() {
    const { id } = useParams<{ id: string }>();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const [invoice, setInvoice] = useState<InvoiceResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showPaymentModal, setShowPaymentModal] = useState(false);

    useHeaderOverride(invoice?.invoice_number, "");

    const fetchInvoice = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        try {
            const data = await getInvoice(id);
            setInvoice(data);
        } catch (err) {
            console.error("[InvoiceDetail] Failed:", err);
            setError(err instanceof Error ? err.message : "Failed to load invoice");
        } finally {
            setIsLoading(false);
        }
    }, [id]);

    useEffect(() => { fetchInvoice(); }, [fetchInvoice]);

    // Handle action=record-payment in query params
    useEffect(() => {
        if (searchParams.get("action") === "record-payment" && invoice) {
            setShowPaymentModal(true);
        }
    }, [searchParams, invoice]);

    const handleMarkSent = async () => {
        if (!invoice) return;
        try {
            await markAsSent(invoice.id);
            fetchInvoice();
        } catch (err) {
            console.error("[InvoiceDetail] Mark sent failed:", err);
        }
    };

    const handleDuplicate = async () => {
        if (!invoice) return;
        try {
            const dup = await duplicateInvoice(invoice.id);
            navigate(`/invoices/${dup.id}/edit`);
        } catch (err) {
            console.error("[InvoiceDetail] Duplicate failed:", err);
        }
    };

    const handleCancel = async () => {
        if (!invoice) return;
        if (!confirm(`Cancel invoice ${invoice.invoice_reference}? This is irreversible.`)) return;
        try {
            await cancelInvoice(invoice.id);
            fetchInvoice();
        } catch (err) {
            console.error("[InvoiceDetail] Cancel failed:", err);
        }
    };

    if (isLoading) {
        return <div className="flex items-center justify-center h-40 text-gray-400">Loading invoice...</div>;
    }

    if (error || !invoice) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-2">
                <p className="text-red-500">{error || "Invoice not found"}</p>
                <Button onClick={() => navigate("/invoices")} className="text-priori-purple">Back to Invoices</Button>
            </div>
        );
    }

    const status = invoice.status.toLowerCase();

    // Build actions
    const actions = [];
    if (status === "draft" || status === "sent") {
        actions.push({ key: "edit", label: "Edit", icon: <Pencil size={16} />, onClick: () => navigate(`/invoices/${invoice.id}/edit`) });
    }
    if (status === "draft") {
        actions.push({ key: "mark-sent", label: "Mark as Sent", icon: <CheckCircle size={16} />, onClick: handleMarkSent });
    }
    if (status !== "canceled") {
        actions.push({ key: "send", label: "Send", icon: <Send size={16} />, onClick: () => alert("Email sending — coming soon") });
    }
    if (["sent", "overdue", "partial", "paid"].includes(status)) {
        actions.push({ key: "payment", label: "Record Payment", icon: <CreditCard size={16} />, onClick: () => setShowPaymentModal(true) });
    }
    actions.push({ key: "pdf", label: "Download PDF", icon: <Download size={16} />, onClick: () => alert("PDF generation — coming soon") });
    actions.push({ key: "duplicate", label: "Duplicate", icon: <Copy size={16} />, onClick: handleDuplicate });
    if (status !== "canceled") {
        actions.push({ key: "cancel", label: "Cancel Invoice", icon: <Ban size={16} />, onClick: handleCancel });
    }

    return (
        <div className="flex flex-col gap-6 font-sans">

            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <Button variant="ghost" onClick={() => navigate("/invoices")}>
                        <ArrowLeft size={20} />
                    </Button>
                </div>
                <Dropdown items={actions} className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors" />
            </div>

            <DocumentViewer
                type="invoice"
                data={{
                    documentReference: invoice.invoice_reference,
                    customerId: invoice.customer_id,
                    customer: invoice.customer,
                    transactionDate: invoice.transaction_date,
                    dueDate: invoice.due_date,
                    currency: invoice.currency,
                    rfqNumber: invoice.rfq_number || undefined,
                    notes: invoice.notes || undefined,
                    discountType: invoice.discount_type as any,
                    discountAmount: invoice.discount_amount,
                    discountPercentage: invoice.discount_percentage,
                    subtotal: invoice.subtotal,
                    taxTotal: invoice.tax_total,
                    totalDue: invoice.total_due,
                    amountPaid: invoice.amount_paid,
                    balanceDue: invoice.balance_due,
                    lineItems: invoice.line_items.map(item => ({
                        id: item.id,
                        itemName: item.description.split('\n')[0] || "",
                        description: item.description,
                        quantity: item.quantity,
                        unitPrice: item.unit_price,
                        taxType: item.tax_type,
                        lineTotal: item.line_total,
                    }))
                }}
            />

            {/* Footer Actions */}
            <div className="flex justify-end items-center gap-4 mt-2">
                <Button
                    variant="outline"
                    onClick={() => alert("Preview not implemented")}
                >
                    Preview
                </Button>
            </div>

            <RecordPaymentModal
                isOpen={showPaymentModal}
                onClose={() => {
                    setShowPaymentModal(false);
                    // Clear the query param
                    navigate(`/invoices/${invoice.id}`, { replace: true });
                }}
                invoice={invoice}
                onSuccess={() => {
                    setShowPaymentModal(false);
                    navigate(`/invoices/${invoice.id}`, { replace: true });
                    fetchInvoice();
                }}
            />
        </div>
    );
}
