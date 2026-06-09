/**
 * InvoiceDetailPage
 */

import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { useHeaderOverride } from "@/components/layout/header-context";
import { RecordPaymentModal } from "@/components/modals/RecordPaymentModal";
import { SendInvoiceModal } from "@/components/modals/SendInvoiceModal";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { useConfirm } from "@/hooks/useConfirm";
import { saveBlob } from "@/lib/utils";
import type { InvoiceResponse } from "@/services/invoiceApi";
import {
    cancelInvoice,
    downloadInvoicePdf,
    duplicateInvoice,
    getInvoice,
    markAsSent,
} from "@/services/invoiceApi";
import {
    ArrowLeft,
    Ban,
    CheckCircle,
    Copy,
    CreditCard,
    Download,
    Pencil,
    Send,
} from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";


export function InvoiceDetailPage() {
    const { id } = useParams<{ id: string }>();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();

    const [invoice, setInvoice] = useState<InvoiceResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [showPaymentModal, setShowPaymentModal] = useState(false);
    const [showSendModal, setShowSendModal] = useState(false);
    const { showConfirm, ConfirmDialog } = useConfirm();

    useHeaderOverride(invoice?.invoice_reference, "");

    const fetchInvoice = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        setError(null);
        try {
            const data = await getInvoice(id);
            setInvoice(data);
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to load invoice"
            );
        } finally {
            setIsLoading(false);
        }
    }, [id]);

    useEffect(() => {
        void (async () => { await fetchInvoice(); })();
    }, [fetchInvoice]);

    useEffect(() => {
        if (searchParams.get("action") === "record-payment" && invoice) {
            startTransition(() => { setShowPaymentModal(true); });
        }
    }, [searchParams, invoice]);

    const handleMarkSent = () => {
        if (!invoice) return;
        showConfirm({
            title: "Mark as Sent",
            description: `Mark invoice ${invoice.invoice_reference} as sent? This will change its status to SENT.`,
            confirmLabel: "Mark as Sent",
            onConfirm: async () => {
                await markAsSent(invoice.id);
                fetchInvoice();
            },
        });
    };

    const handleDuplicate = async () => {
        if (!invoice) return;
        try {
            const dup = await duplicateInvoice(invoice.id);
            navigate(`/invoices/${dup.new_invoice_id}/edit`);
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to duplicate invoice"
            );
        }
    };

    const handleCancel = () => {
        if (!invoice) return;
        showConfirm({
            title: "Cancel Invoice",
            description: `Cancel invoice ${invoice.invoice_reference}? This action is irreversible and cannot be undone.`,
            confirmLabel: "Cancel Invoice",
            variant: "danger",
            onConfirm: async () => {
                await cancelInvoice(invoice.id);
                fetchInvoice();
            },
        });
    };

    const handleDownloadPdf = async () => {
        if (!invoice) return;
        try {
            const blob = await downloadInvoicePdf(invoice.id);
            saveBlob(blob, `Invoice_${invoice.invoice_reference}.pdf`);
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to download PDF"
            );
        }
    };

    const handlePaymentClose = () => {
        setShowPaymentModal(false);
        if (id) navigate(`/invoices/${id}`, { replace: true });
    };

    const handlePaymentSuccess = () => {
        setShowPaymentModal(false);
        if (id) navigate(`/invoices/${id}`, { replace: true });
        fetchInvoice();
    };

    if (isLoading) {
        return <LoadingState message="Loading invoice..." />;
    }

    if (error || !invoice) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-4">
                <p className="text-red-500">{error ?? "Invoice not found"}</p>
                <Button variant="outline" onClick={() => navigate("/invoices")}>
                    <ArrowLeft size={16} /> Back to Invoices
                </Button>
            </div>
        );
    }

    const status = invoice.status.toLowerCase();
    const actions: DropdownItem[] = [];

    if (invoice.is_editable) {
        actions.push({
            key: "edit",
            label: "Edit",
            icon: <Pencil size={16} />,
            onClick: () => navigate(`/invoices/${invoice.id}/edit`),
        });
    }

    if (status === "draft") {
        actions.push({
            key: "mark-sent",
            label: "Mark as Sent",
            icon: <CheckCircle size={16} />,
            onClick: handleMarkSent,
        });
    }

    if (status !== "canceled") {
        actions.push({
            key: "send",
            label: "Send",
            icon: <Send size={16} />,
            onClick: () => setShowSendModal(true),
        });
    }

    if (["sent", "overdue", "partial"].includes(status)) {
        actions.push({
            key: "payment",
            label: "Record Payment",
            icon: <CreditCard size={16} />,
            onClick: () => setShowPaymentModal(true),
        });
    }

    actions.push({
        key: "pdf",
        label: "Download PDF",
        icon: <Download size={16} />,
        onClick: handleDownloadPdf,
    });

    actions.push({
        key: "duplicate",
        label: "Duplicate",
        icon: <Copy size={16} />,
        onClick: handleDuplicate,
    });

    if (status !== "canceled") {
        actions.push({
            key: "cancel",
            label: "Cancel Invoice",
            icon: <Ban size={16} />,
            onClick: handleCancel,
            danger: true,
        });
    }

    return (
        <div className="flex flex-col gap-6 font-sans">

            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => navigate("/invoices")}
                        aria-label="Back to invoices"
                    >
                        <ArrowLeft size={20} />
                    </Button>
                    <Badge
                        variant={
                            invoice.is_overdue
                                ? "overdue"
                                : (status as BadgeVariant)
                        }
                    >
                        {invoice.is_overdue
                            ? `Overdue (${invoice.days_overdue}d)`
                            : invoice.status}
                    </Badge>
                </div>

                <Dropdown
                    items={actions}
                    className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
                />
            </div>

            {/* Error banner */}
            {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                    {error}
                </div>
            )}

            {/* Document Viewer */}
            <DocumentViewer
                type="invoice"
                data={{
                    documentReference: invoice.invoice_reference,
                    customerId: invoice.customer_id,
                    customer: invoice.customer,
                    transactionDate: invoice.transaction_date,
                    dueDate: invoice.due_date,
                    currency: invoice.currency,
                    rfqNumber: invoice.rfq_number ?? undefined,
                    notes: invoice.notes ?? undefined,
                    discountType: invoice.discount_type as
                        | "amount"
                        | "percentage"
                        | null,
                    discountAmount: invoice.discount_amount,
                    discountPercentage: invoice.discount_percentage,
                    subtotal: invoice.subtotal,
                    taxTotal: invoice.tax_total,
                    totalDue: invoice.total_due,
                    amountPaid: invoice.amount_paid,
                    balanceDue: invoice.balance_due,
                    lineItems: invoice.line_items.map((item) => ({
                        id: item.id,
                        itemName: item.item_name,
                        description: item.description,
                        quantity: item.quantity,
                        unitPrice: item.unit_price,
                        taxType: item.tax_type,
                        lineTotal: item.line_total,
                    })),
                }}
            />

            {/* Footer */}
            <div className="flex justify-end items-center gap-4">
                <Button
                    variant="outline"
                    onClick={() => navigate(`/invoices/${invoice.id}/edit`)}
                    disabled={!invoice.is_editable}
                >
                    <Pencil size={16} /> Edit
                </Button>
            </div>

            {/* Record Payment Modal */}
            {invoice && (
                <RecordPaymentModal
                    isOpen={showPaymentModal}
                    onClose={handlePaymentClose}
                    entityId={invoice.id}
                    entityType="invoice"
                    balanceDue={invoice.balance_due}
                    currency={invoice.currency}
                    reference={invoice.invoice_reference}
                    onSuccess={handlePaymentSuccess}
                />
            )}

            {/* Send Invoice Modal */}
            <SendInvoiceModal
                isOpen={showSendModal}
                onClose={() => setShowSendModal(false)}
                invoice={invoice}
                onSuccess={() => {
                    setShowSendModal(false);
                    fetchInvoice();
                }}
            />

            {/* Confirm Dialog */}
            {ConfirmDialog}
        </div>
    );
}