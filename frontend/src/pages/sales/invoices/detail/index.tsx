/**
 * InvoiceDetailPage
 */

import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { useHeaderOverride } from "@/components/layout/header-context";
import { RecordPaymentModal } from "@/components/modals/RecordPaymentModal";
import { SendInvoiceModal } from "@/components/modals/SendInvoiceModal";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { Table } from "@/components/ui/Table";
import { useConfirm } from "@/hooks/useConfirm";
import { DEFAULT_CURRENCY } from "@/lib/constants";
import { formatCurrency, formatDisplayDate, getNameInitials, saveBlob } from "@/lib/utils";
import type { InvoiceResponse, Payment } from "@/services/invoiceApi";
import {
    cancelInvoice,
    deleteInvoice,
    downloadInvoicePdf,
    duplicateInvoice,
    getInvoice,
    markAsSent,
} from "@/services/invoiceApi";
import {
    ArrowLeft,
    CheckCircle,
    Copy,
    CreditCard,
    Download,
    Pencil,
    Send,
    Trash,
} from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

type DetailTab = "overview" | "detail";
const PAYMENTS_PER_PAGE = 5;

export function InvoiceDetailPage() {
    const { id } = useParams<{ id: string }>();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();

    const [invoice, setInvoice] = useState<InvoiceResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [showSendModal, setShowSendModal] = useState(false);
    const [mainTab, setMainTab] = useState<DetailTab>("overview");

    const [showPaymentModal, setShowPaymentModal] = useState(false);
    const [paymentsPage, setPaymentsPage] = useState(1);
    const [paymentsPerPage, setPaymentsPerPage] = useState(PAYMENTS_PER_PAGE);


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

    const handleDelete = () => {
        if (!invoice) return;
        const isDraft = invoice.status.toLowerCase() === "draft";
        showConfirm({
            title: isDraft ? "Delete invoice?" : "Cancel invoice?",
            description: isDraft
                ? "Are you sure you want to delete this invoice? This action cannot be undone."
                : `Cancel invoice ${invoice.invoice_reference}? This will void the invoice and it can no longer be paid.`,
            confirmLabel: isDraft ? "Yes, delete it" : "Yes, cancel it",
            variant: "danger",
            onConfirm: async () => {
                try {
                    if (isDraft) {
                        await deleteInvoice(invoice.id);
                    } else {
                        await cancelInvoice(invoice.id);
                    }
                    fetchInvoice();
                } catch (err) {
                    setError(err instanceof Error ? err.message : (isDraft ? "Failed to delete invoice" : "Failed to cancel invoice"));
                }
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

    const customer = invoice.customer;
    const status = invoice.status.toLowerCase();

    const currency = invoice.currency ?? invoice.customer.currency ?? DEFAULT_CURRENCY
    const total = Number(invoice.total_due);
    const amountPaid = Number(invoice.amount_paid ?? 0);
    const balanceDue = Number(invoice.balance_due ?? total - amountPaid);
    const isOverpaid = balanceDue < 0;
    const payments: Payment[] = invoice.payments ?? [];

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

    if (["draft", "sent", "partial", "overdue"].includes(status)) {
        actions.push({
            key: "delete",
            label: status === "draft" ? "Delete Invoice" : "Cancel Invoice",
            icon: <Trash size={16} />,
            onClick: handleDelete,
            danger: true,
        });
    }

    const totalPaymentPages = Math.max(1, Math.ceil(payments.length / paymentsPerPage));
    const currentPaymentsPage = Math.min(paymentsPage, totalPaymentPages);
    const pagedPayments = payments.slice(
        (currentPaymentsPage - 1) * paymentsPerPage,
        currentPaymentsPage * paymentsPerPage,
    );
    const pagedPaymentsTotal = pagedPayments.reduce(
        (sum, payment) => sum + Number(payment.amount ?? 0),
        0,
    );

    const paymentColumns = [
        {
            key: "number",
            header: "#.",
            render: (_payment: Payment, index: number) => (
                <span className="text-content-primary">
                    {(currentPaymentsPage - 1) * paymentsPerPage + index + 1}.
                </span>
            ),
            className: "w-[50px]",
        },
        {
            key: "date",
            header: "Date",
            render: (payment: Payment) => (
                <span className="text-content-primary">
                    {payment.payment_date ? formatDisplayDate(payment.payment_date) : "-"}
                </span>
            ),
        },
        {
            key: "reference",
            header: "Payment Ref #",
            render: (payment: Payment) => (
                <span className="text-gray-500 truncate" title={payment.reference ?? undefined}>
                    {payment.reference ?? "-"}
                </span>
            ),
        },
        {
            key: "amount",
            header: "Amount",
            render: (payment: Payment) => (
                <span className="text-gray-500">{formatCurrency(Number(payment.amount), currency)}</span>
            ),
        },

    ];

    return (
        <div className="flex flex-col gap-6 font-sans">

            {/* Error banner */}
            {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                    {error}
                </div>
            )}
            <div className="flex items-center justify-between">
                {/* Header */}
                <div className="flex items-center gap-2">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => navigate("/invoices")}
                        aria-label="Back to invoices list"
                    >
                        <ArrowLeft size={20} />
                    </Button>

                    <div className="flex items-center gap-1 border-b border-gray-200">
                        <button
                            onClick={() => setMainTab("overview")}
                            className={`px-6 py-3 font-semibold transition-colors relative ${mainTab === "overview"
                                ? "text-priori-purple"
                                : "text-gray-500 hover:text-gray-700"
                                }`}
                        >
                            Overview
                            {mainTab === "overview" && (
                                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-priori-purple" />
                            )}
                        </button>
                        <button
                            onClick={() => setMainTab("detail")}
                            className={`px-6 py-3 font-semibold transition-colors relative ${mainTab === "detail"
                                ? "text-priori-purple"
                                : "text-gray-500 hover:text-gray-700"
                                }`}
                        >
                            Detail
                            {mainTab === "detail" && (
                                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-priori-purple" />
                            )}
                        </button>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    {actions.length > 0 && (
                        <Dropdown
                            items={actions}
                            className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
                        />
                    )}
                </div>
            </div>

            {/* Document Viewer */}
            {mainTab === "overview" && (
                <>
                    <DocumentViewer
                        type="invoice"
                        data={{
                            documentReference: invoice.invoice_reference,
                            customerId: invoice.customer_id,
                            customer: invoice.customer,
                            transactionDate: invoice.transaction_date,
                            dueDate: invoice.due_date,
                            currency: invoice.currency,
                            // rfqNumber: invoice.rfq_number ?? undefined,
                            notes: invoice.notes ?? undefined,
                            discountType: invoice.discount_type as
                                | "amount"
                                | "percentage"
                                | null,
                            discountAmount: invoice.discount_amount == null ? invoice.discount_amount : Number(invoice.discount_amount),
                            discountPercentage: invoice.discount_percentage == null ? invoice.discount_percentage : Number(invoice.discount_percentage),
                            subtotal: Number(invoice.subtotal),
                            taxTotal: Number(invoice.tax_total),
                            totalDue: Number(invoice.total_due),
                            amountPaid: Number(invoice.amount_paid),
                            balanceDue: Number(invoice.balance_due),
                            lineItems: (invoice.line_items ?? []).map((item) => ({
                                id: item.id,
                                itemName: item.item_name,
                                description: item.description,
                                quantity: Number(item.quantity),
                                unitPrice: Number(item.unit_price),
                                taxType: item.tax_type,
                                lineTotal: Number(item.line_total),
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
                </>
            )}


            {/* Detail Tab Content */}
            {mainTab === "detail" && (
                <div className="grid grid-cols-1 lg:grid-cols-10 gap-6">
                    {/* Left Column: Customer Info Card */}
                    <div className="rounded-2xl border border-gray-200 bg-white space-y-6 lg:col-span-3">
                        <div className="flex items-start gap-3 px-4 py-3  border-b border-gray-100">
                            <p className="flex items-center justify-center bg-purple-25 w-12 h-12 rounded-full text-priori-purple font-bold text-[14px]">
                                {getNameInitials(customer.display_name)}
                            </p>
                            <div className="flex-1">
                                <p className="font-bold text-gray-800 text-[18px]">
                                    {customer.display_name}
                                </p>
                                <p className="text-[14px] text-gray-400">
                                    Customer Since {customer.created_at
                                        ? new Date(customer.created_at).getFullYear()
                                        : new Date().getFullYear()}
                                </p>
                            </div>
                        </div>

                        {/* Contact Information */}
                        {customer.email && (
                            <div className="p-4">
                                <p className="text-gray-500 text-[14px]">Email</p>
                                <p className="text-gray-800 font-bold text-[16px]">
                                    {customer.email}
                                </p>
                            </div>
                        )}
                        {customer.phone && (
                            <div className="p-4">
                                <p className="text-gray-500 text-[14px]">Phone</p>
                                <p className="text-gray-800 font-bold text-[16px]">
                                    {customer.phone}
                                </p>
                            </div>
                        )}
                        {customer.currency && (
                            <div className="p-4">
                                <p className="text-gray-500 text-[14px]">Currency</p>
                                <p className="text-gray-800 font-bold text-[16px]">
                                    {customer.currency}
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Right Column: Stats Cards & Invoice Table */}
                    <div className="lg:col-span-7 space-y-6">
                        <div className="grid grid-cols-2 gap-6">
                            <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                                <p className="text-gray-500 text-lg py-3">Total</p>
                                <p className="font-bold text-gray-800 text-2xl">
                                    {formatCurrency(Number(total), currency ?? "")}
                                </p>
                            </Card>
                            <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                                <p className="text-gray-500 text-lg py-3 flex items-center gap-2">
                                    {isOverpaid ? "Balance (Overpaid)" : "Balance"}
                                </p>
                                <p className={`font-bold text-2xl ${isOverpaid ? "text-priori-purple" : "text-gray-800"}`}>
                                    {formatCurrency(Number(balanceDue), currency ?? "")}
                                </p>
                            </Card>
                        </div>

                        <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
                            <h3 className="text-[18px] font-bold text-gray-800">Payments</h3>

                            <div className="overflow-x-auto rounded-b-lg">
                                <Table
                                    columns={paymentColumns}
                                    data={pagedPayments}
                                    rowKey={(payment) => payment.id}
                                    emptyMessage="No payments recorded yet."
                                />

                                {/* Totals row: sum of the Amount column for the rows currently displayed (respects the active pagination window). */}
                                {pagedPayments.length > 0 && (
                                    <div className="flex items-center justify-between border-t border-gray-200 px-3 py-3">
                                        <span className="text-sm font-semibold text-gray-700">
                                            Total
                                        </span>
                                        <span className="text-sm font-bold text-gray-800">
                                            {formatCurrency(pagedPaymentsTotal, currency)}
                                        </span>
                                    </div>
                                )}
                            </div>
                        </div>
                        {/* Pagination controls — shown only when there is more than a single page of payments. */}
                        {payments.length > 0 && (
                            <div className="py-3 px-4 border border-gray-200 bg-white rounded-2xl">
                                <Pagination
                                    currentPage={currentPaymentsPage}
                                    totalPages={totalPaymentPages}
                                    perPage={paymentsPerPage}
                                    onPageChange={setPaymentsPage}
                                    onPerPageChange={(nextPerPage) => {
                                        setPaymentsPerPage(nextPerPage);
                                        setPaymentsPage(1);
                                    }}
                                />
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Record Payment Modal */}
            {invoice && (
                <RecordPaymentModal
                    isOpen={showPaymentModal}
                    onClose={handlePaymentClose}
                    entityId={invoice.id}
                    entityType="invoice"
                    balanceDue={Number(invoice.balance_due)}
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