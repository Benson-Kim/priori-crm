import { PurchaseOrderViewer } from "@/components/documents/PurchaseOrderViewer";
import { useHeaderOverride } from "@/components/layout/header-context";
import { PurchaseOrderPaymentModal } from "@/components/modals/PurchaseOrderPaymentModal";
import { RecordPaymentModal } from "@/components/modals/RecordPaymentModal";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { Table } from "@/components/ui/Table";
import { useConfirm } from "@/hooks/useConfirm";
import { DEFAULT_CURRENCY } from "@/lib/constants";
import { formatCurrency, formatDisplayDate, saveBlob } from "@/lib/utils";
import type {
    PurchaseOrderLineItem,
    PurchaseOrderPayment,
    PurchaseOrderResponse,
} from "@/services/purchaseOrderApi";
import {
    deletePurchaseOrder,
    downloadPurchaseOrderStatementPdf,
    duplicatePurchaseOrder,
    exportPurchaseOrderPaymentsExcel,
    getPurchaseOrder,
    markAsSentPurchaseOrder,
    sendPurchaseOrder,
} from "@/services/purchaseOrderApi";
import {
    ArrowLeft,
    CheckCircle,
    ChevronLeft,
    ChevronRight,
    Copy,
    CreditCard,
    Eye,
    FileClock,
    FileSpreadsheetIcon,
    Paperclip,
    Pencil,
    Send,
    Trash,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

type DetailTab = "overview" | "detail";

/** Recorded payments shown per page in the Detail-tab payments table. */
const PAYMENTS_PER_PAGE = 5;

export default function PurchaseOrderDetailPage() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();

    const [po, setPo] = useState<PurchaseOrderResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<DetailTab>("overview");

    const { showConfirm, ConfirmDialog } = useConfirm();

    const [isPaymentModalOpen, setIsPaymentModalOpen] = useState(false);
    // Selected payment row — opens the view/attach-document modal.
    const [selectedPayment, setSelectedPayment] = useState<PurchaseOrderPayment | null>(null);
    // Current page (1-based) of the payments table.
    const [paymentsPage, setPaymentsPage] = useState(1);

    useHeaderOverride(po?.po_reference, "");

    const fetchPurchaseOrder = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        setError(null);
        try {
            const data = await getPurchaseOrder(id);
            setPo(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load purchase order");
        } finally {
            setIsLoading(false);
        }
    }, [id]);

    useEffect(() => {
        void (async () => { await fetchPurchaseOrder(); })();
    }, [fetchPurchaseOrder]);

    const handleSend = () => {
        if (!po) return;
        showConfirm({
            title: "Send purchase order?",
            description: `Send ${po.po_reference} to the vendor by email? It will be marked as Sent.`,
            confirmLabel: "Yes, send",
            onConfirm: async () => {
                try {
                    await sendPurchaseOrder(po.id);
                    fetchPurchaseOrder();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to send purchase order");
                }
            },
        });
    };

    const handleMarkAsSent = () => {
        if (!po) return;
        showConfirm({
            title: "Mark as sent?",
            description: "Are you sure you want to mark this item as sent?",
            confirmLabel: "Yes, mark as sent",
            onConfirm: async () => {
                try {
                    await markAsSentPurchaseOrder(po.id);
                    fetchPurchaseOrder();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to send purchase order");
                }
            },
        });
    };

    const handleDuplicate = async () => {
        if (!po) return;
        try {
            const result = await duplicatePurchaseOrder(po.id);
            navigate(`/purchase-orders/${result.new_po_id}/edit`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to duplicate purchase order");
        }
    };

    const handleDelete = () => {
        if (!po) return;
        showConfirm({
            title: "Delete purchase order?",
            description: `Delete ${po.po_reference}? This action is irreversible.`,
            confirmLabel: "Delete",
            variant: "danger",
            onConfirm: async () => {
                await deletePurchaseOrder(po.id);
                navigate("/purchase-orders");
            },
        });
    };

    /** Current PDF: payments applied + running balance (statement variant). */
    const handleDownloadStatement = async () => {
        if (!po) return;
        try {
            const blob = await downloadPurchaseOrderStatementPdf(po.id);
            saveBlob(blob, `PurchaseOrder_${po.po_reference}_Statement.pdf`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to download statement");
        }
    };

    /** Export this PO's recorded payments to an Excel workbook. */
    const handlePaymentsExport = async () => {
        if (!po) return;
        try {
            const blob = await exportPurchaseOrderPaymentsExcel(po.id);
            saveBlob(blob, `${po.po_reference}_Payments_${new Date().toISOString().split("T")[0]}.xlsx`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to export payments");
        }
    };

    if (isLoading) {
        return <LoadingState message="Loading purchase order..." />;
    }

    if (error || !po) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-4">
                <p className="text-red-500">{error ?? "Purchase order not found"}</p>
                <Button variant="outline" onClick={() => navigate("/purchase-orders")}>
                    <ArrowLeft size={16} /> Back to Purchase Orders
                </Button>
            </div>
        );
    }

    const status = po.status.toLowerCase();
    const currency = po.currency ?? DEFAULT_CURRENCY;
    const total = Number(po.total);
    const amountPaid = Number(po.amount_paid ?? 0);
    const balanceDue = Number(po.balance_due ?? total - amountPaid);
    const payments: PurchaseOrderPayment[] = po.payments ?? [];
    const documents = po.documents ?? [];

    // Actions are scoped to the active tab so each tab surfaces only its
    // logical actions:
    //  - Overview (the document view): document-lifecycle actions.
    //  - Detail (the financial view): payment + statement/export actions.
    const actions: DropdownItem[] = [];

    if (activeTab === "overview") {
        // Draft-only: editable.
        if (po.is_editable) {
            actions.push({
                key: "edit",
                label: "Edit",
                icon: <Pencil size={16} />,
                onClick: () => navigate(`/purchase-orders/${po.id}/edit`),
            });
        }
        // Sendable (email) and mark-as-sent (offline) while not yet sent.
        if (status !== "sent") {
            actions.push({
                key: "send",
                label: "Send",
                icon: <Send size={16} />,
                onClick: handleSend,
            });
            actions.push({
                key: "mark-as-sent",
                label: "Mark as sent",
                icon: <CheckCircle size={16} />,
                onClick: handleMarkAsSent,
            });
        }
        // Duplicate is available at any status.
        actions.push({
            key: "duplicate",
            label: "Duplicate",
            icon: <Copy size={16} />,
            onClick: handleDuplicate,
        });
        // Delete: DRAFT only (SENT/PAID are protected records).
        if (status === "draft") {
            actions.push({
                key: "delete",
                label: "Delete",
                icon: <Trash size={16} />,
                danger: true,
                onClick: handleDelete,
            });
        }
    } else {
        // Detail tab: financial actions only.
        // Record payment: SENT only, while there is still a balance to clear.
        if (status === "sent" && balanceDue > 0) {
            actions.push({
                key: "record-payment",
                label: "Record payment",
                icon: <CreditCard size={16} />,
                onClick: () => setIsPaymentModalOpen(true),
            });
        }
        // Balance-aware statement PDF (payments applied + running balance).
        actions.push({
            key: "download-statement",
            label: "Download statement",
            icon: <FileClock size={16} />,
            onClick: handleDownloadStatement,
        });
        // Export this PO's payments to Excel.
        actions.push({
            key: "export-po-payments-excel",
            label: "Export payments",
            icon: <FileSpreadsheetIcon size={16} />,
            onClick: handlePaymentsExport,
        });
    }

    const hasDocument = (payment: PurchaseOrderPayment) =>
        Boolean(payment.document_id && documents.some((d) => d.id === payment.document_id));

    // Client-side pagination for the payments table (PAYMENTS_PER_PAGE rows).
    const totalPaymentPages = Math.max(1, Math.ceil(payments.length / PAYMENTS_PER_PAGE));
    const currentPaymentsPage = Math.min(paymentsPage, totalPaymentPages);
    const pagedPayments = payments.slice(
        (currentPaymentsPage - 1) * PAYMENTS_PER_PAGE,
        currentPaymentsPage * PAYMENTS_PER_PAGE,
    );

    // Payments table columns — mirrors the invoices/PO list table format. The
    // row number is offset by the current page so it reflects the absolute
    // position across all payments, not the position within the page.
    const paymentColumns = [
        {
            key: "number",
            header: "#.",
            render: (_payment: PurchaseOrderPayment, index: number) => (
                <span className="text-content-primary">
                    {(currentPaymentsPage - 1) * PAYMENTS_PER_PAGE + index + 1}.
                </span>
            ),
            className: "w-[50px]",
        },
        {
            key: "date",
            header: "Date",
            render: (payment: PurchaseOrderPayment) => (
                <span className="text-content-primary">
                    {payment.payment_date ? formatDisplayDate(payment.payment_date) : "-"}
                </span>
            ),
        },
        {
            key: "amount",
            header: "Amount",
            render: (payment: PurchaseOrderPayment) => (
                <span className="text-gray-500">{formatCurrency(Number(payment.amount), currency)}</span>
            ),
        },
        {
            key: "reference",
            header: "Reference",
            render: (payment: PurchaseOrderPayment) => (
                <span className="text-gray-500 truncate" title={payment.reference ?? undefined}>
                    {payment.reference ?? "-"}
                </span>
            ),
        },
        {
            key: "document",
            header: "Document",
            render: (payment: PurchaseOrderPayment) =>
                hasDocument(payment) ? (
                    <span className="inline-flex items-center gap-1 text-priori-purple">
                        <Paperclip size={16} /> Attached
                    </span>
                ) : (
                    <span className="text-gray-400">—</span>
                ),
        },
        {
            key: "actions",
            header: "Actions",
            render: (payment: PurchaseOrderPayment) =>
                <button
                    type="button"
                    onClick={(e) => {
                        // Row also has an onRowClick handler; stop the event
                        // bubbling so the modal isn't opened twice.
                        e.stopPropagation();
                        setSelectedPayment(payment);
                    }}
                    className="inline-flex items-center gap-2.5 px-3 py-2 text-sm text-priori-purple transition-colors cursor-pointer hover:underline"
                >
                    <Eye size={16} /> View
                </button>
        }
    ];

    return (
        <div className="flex flex-col gap-6 font-sans pb-10">
            {/* Header: back + tabs + action dropdown */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => navigate("/purchase-orders")}
                        aria-label="Back to purchase orders"
                    >
                        <ArrowLeft size={20} />
                    </Button>

                    <div className="flex items-center gap-1 border-b border-gray-200">
                        <button
                            onClick={() => setActiveTab("overview")}
                            className={`px-6 py-3 font-semibold transition-colors relative ${activeTab === "overview"
                                ? "text-priori-purple"
                                : "text-gray-500 hover:text-gray-700"
                                }`}
                        >
                            Overview
                            {activeTab === "overview" && (
                                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-priori-purple" />
                            )}
                        </button>
                        <button
                            onClick={() => setActiveTab("detail")}
                            className={`px-6 py-3 font-semibold transition-colors relative ${activeTab === "detail"
                                ? "text-priori-purple"
                                : "text-gray-500 hover:text-gray-700"
                                }`}
                        >
                            Detail
                            {activeTab === "detail" && (
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

            {/* Error banner */}
            {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                    {error}
                </div>
            )}

            {/* Overview tab: descriptive header + totals. */}
            {activeTab === "overview" && (
                <PurchaseOrderViewer
                    editableOwner
                    data={{
                        poReference: po.po_reference,
                        vendorId: po.vendor_id,
                        vendor: po.vendor,
                        orderDate: po.order_date,
                        deliveryDate: po.delivery_date,
                        currency: po.currency,
                        isRecurring: po.is_recurring,
                        complianceRef: po.compliance_ref,
                        notes: po.notes || undefined,
                        termsAndConditions: po.terms_and_conditions,
                        subtotal: Number(po.subtotal),
                        taxTotal: Number(po.tax_total),
                        total: Number(po.total),
                        lineItems: (po.line_items ?? []).map((item: PurchaseOrderLineItem, index) => ({
                            id: item.id ?? `line-${index}`,
                            itemName: item.item_name,
                            description: item.description,
                            quantity: Number(item.quantity),
                            unitPrice: Number(item.unit_price),
                            taxType: item.tax_type,
                            lineTotal: Number(item.line_total ?? Number(item.quantity) * Number(item.unit_price)),
                        })),
                    }}
                />
            )}

            {/* Detail tab: vendor summary + running totals + paginated payments. */}
            {activeTab === "detail" && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                    {/* LEFT: vendor + key dates. */}
                    <div className="rounded-2xl border border-gray-200 bg-white flex flex-col gap-6 h-fit">
                        <div className="flex items-start gap-3 px-4 py-3 border-b border-gray-100">
                            <div className="min-w-0">
                                <p className="text-[14px] text-gray-500">Vendor</p>
                                <p className="text-[20px] font-bold text-gray-800 truncate">
                                    {po.vendor?.vendor_name ?? po.vendor_id}
                                </p>
                            </div>
                        </div>

                        <div className="grid grid-cols-1 gap-x-8 gap-y-4 pb-4">
                            <OverviewField label="Reference" value={po.po_reference} />
                            <OverviewField
                                label="Order Date"
                                value={po.order_date ? formatDisplayDate(po.order_date) : "-"}
                            />
                            <OverviewField
                                label="Delivery Date"
                                value={po.delivery_date ? formatDisplayDate(po.delivery_date) : "-"}
                            />
                        </div>
                    </div>

                    {/* RIGHT: totals + recorded payments table (paginated). Click a
                        row to view the payment and its related document. */}
                    <div className="lg:col-span-2 space-y-4">
                        <div className="grid grid-cols-2 gap-6">
                            <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                                <p className="text-gray-500 text-lg py-3">Total</p>
                                <p className="font-bold text-gray-800 text-2xl">
                                    {formatCurrency(Number(total), currency ?? "")}
                                </p>
                            </Card>
                            <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                                <p className="text-gray-500 text-lg py-3">Balance</p>
                                <p className="font-bold text-gray-800 text-2xl">
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
                                    onRowClick={(payment) => setSelectedPayment(payment)}
                                    emptyMessage="No payments recorded yet."
                                />
                            </div>

                            {/* Pagination controls — shown only when there is more
                                than a single page of payments. */}
                            {payments.length > PAYMENTS_PER_PAGE && (
                                <div className="flex items-center justify-between pt-2">
                                    <p className="text-sm text-gray-500">
                                        Page {currentPaymentsPage} of {totalPaymentPages}
                                    </p>
                                    <div className="flex items-center gap-2">
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={currentPaymentsPage <= 1}
                                            onClick={() => setPaymentsPage((p) => Math.max(1, p - 1))}
                                            aria-label="Previous page"
                                        >
                                            <ChevronLeft size={16} /> Previous
                                        </Button>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={currentPaymentsPage >= totalPaymentPages}
                                            onClick={() => setPaymentsPage((p) => Math.min(totalPaymentPages, p + 1))}
                                            aria-label="Next page"
                                        >
                                            Next <ChevronRight size={16} />
                                        </Button>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* Payment detail modal: view a recorded payment and attach its
                related proof-of-payment document. */}
            <PurchaseOrderPaymentModal
                isOpen={selectedPayment !== null}
                onClose={() => setSelectedPayment(null)}
                poId={po.id}
                payment={selectedPayment}
                documents={documents}
                currency={currency}
                onUpdated={() => {
                    fetchPurchaseOrder();
                }}
            />

            {/* Record payment modal */}
            <RecordPaymentModal
                isOpen={isPaymentModalOpen}
                onClose={() => setIsPaymentModalOpen(false)}
                entityId={po.id}
                entityType="purchaseOrder"
                balanceDue={balanceDue}
                currency={currency}
                reference={po.po_reference}
                onSuccess={() => {
                    setIsPaymentModalOpen(false);
                    fetchPurchaseOrder();
                }}
            />

            {/* Confirm Dialog */}
            {ConfirmDialog}
        </div>
    );
}

function OverviewField({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div className="min-w-0 p-4">
            <p className="text-[14px] text-gray-500">{label}</p>
            <p className="text-[14px] text-gray-800 font-bold truncate">{value}</p>
        </div>
    );
}
