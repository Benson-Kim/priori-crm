import { PurchaseOrderViewer } from "@/components/documents/PurchaseOrderViewer";
import { useHeaderOverride } from "@/components/layout/header-context";
import { PurchaseOrderPaymentModal } from "@/components/modals/PurchaseOrderPaymentModal";
import { RecordPaymentModal } from "@/components/modals/RecordPaymentModal";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Divider } from "@/components/ui/Divider";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { Table } from "@/components/ui/Table";
import { useConfirm } from "@/hooks/useConfirm";
import { ACCEPTED_UPLOAD_TYPES } from "@/lib/constants";
import { formatCurrency, formatDisplayDate, saveBlob } from "@/lib/utils";
import type {
    PurchaseOrderLineItem,
    PurchaseOrderPayment,
    PurchaseOrderResponse,
} from "@/services/purchaseOrderApi";
import {
    deletePurchaseOrder,
    deletePurchaseOrderDocument,
    downloadPurchaseOrderDocument,
    downloadPurchaseOrderPdf,
    downloadPurchaseOrderStatementPdf,
    duplicatePurchaseOrder,
    getPurchaseOrder,
    markAsSentPurchaseOrder,
    sendPurchaseOrder,
    uploadPurchaseOrderDocument,
} from "@/services/purchaseOrderApi";
import {
    ArrowLeft,
    CheckCircle,
    Copy,
    CreditCard,
    Download,
    FileText,
    Paperclip,
    PaperclipIcon,
    Pencil,
    Plus,
    Send,
    Trash,
    X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

type DetailTab = "overview" | "detail";

export default function PurchaseOrderDetailPage() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();

    const [po, setPo] = useState<PurchaseOrderResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<DetailTab>("overview");

    const { showConfirm, ConfirmDialog } = useConfirm();

    // Document upload state
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);
    // Two document-level downloads: original (full amount) and current (balance-aware).
    const [isDownloadingOriginal, setIsDownloadingOriginal] = useState(false);
    const [isDownloadingCurrent, setIsDownloadingCurrent] = useState(false);
    const [isPaymentModalOpen, setIsPaymentModalOpen] = useState(false);
    // Selected payment row — opens the view/update (attach document) modal.
    const [selectedPayment, setSelectedPayment] = useState<PurchaseOrderPayment | null>(null);

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
            description: `Mark ${po.po_reference} as Sent without emailing the vendor? Use this when the PO was sent offline.`,
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

    /** Original PO PDF: full amount, no payments applied. */
    const handleDownloadOriginal = async () => {
        if (!po) return;
        setIsDownloadingOriginal(true);
        try {
            const blob = await downloadPurchaseOrderPdf(po.id);
            saveBlob(blob, `PurchaseOrder_${po.po_reference}.pdf`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to download PDF");
        } finally {
            setIsDownloadingOriginal(false);
        }
    };

    /** Current PDF: payments applied + running balance (statement variant). */
    const handleDownloadCurrent = async () => {
        if (!po) return;
        setIsDownloadingCurrent(true);
        try {
            const blob = await downloadPurchaseOrderStatementPdf(po.id);
            saveBlob(blob, `PurchaseOrder_${po.po_reference}_Current.pdf`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to download current PDF");
        } finally {
            setIsDownloadingCurrent(false);
        }
    };

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !id) return;
        try {
            setIsUploading(true);
            await uploadPurchaseOrderDocument(id, file, "view");
            fetchPurchaseOrder();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to upload document");
        } finally {
            setIsUploading(false);
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        }
    };

    const handleFileDelete = (docId: string, filename: string) => {
        if (!id) return;
        showConfirm({
            title: "Delete Document",
            description: `Are you sure you want to delete ${filename}?`,
            confirmLabel: "Delete",
            variant: "danger",
            onConfirm: async () => {
                await deletePurchaseOrderDocument(id, docId);
                fetchPurchaseOrder();
            },
        });
    };

    const handleFileDownload = (docId: string, filename: string) => {
        if (!id) return;
        showConfirm({
            title: "Download Document",
            description: `Are you sure you want to download ${filename}?`,
            confirmLabel: "Download",
            variant: "default",
            onConfirm: async () => {
                try {
                    const blob = await downloadPurchaseOrderDocument(id, docId);
                    saveBlob(blob, filename);
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to download document");
                }
            },
        });
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
    const currency = po.currency ?? "Ksh";
    // Balance fields land with PO-19; default defensively so the UI is correct
    // even before any payment has been recorded.
    const total = Number(po.total);
    const amountPaid = Number(po.amount_paid ?? 0);
    const balanceDue = Number(po.balance_due ?? total - amountPaid);
    const payments: PurchaseOrderPayment[] = po.payments ?? [];

    const actions: DropdownItem[] = [];

    // Draft-only: editable, sendable (email) and mark-as-sent (offline, no email).
    if (po.is_editable) {
        actions.push({
            key: "edit",
            label: "Edit",
            icon: <Pencil size={16} />,
            onClick: () => navigate(`/purchase-orders/${po.id}/edit`),
        });
    }
    // if (status === "draft") {
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
    // }

    // Record payment: SENT only, while there is still a balance to clear.
    if (status === "sent" && balanceDue > 0) {
        actions.push({
            key: "record-payment",
            label: "Record payment",
            icon: <CreditCard size={16} />,
            onClick: () => setIsPaymentModalOpen(true),
        });
    }

    // Duplicate is available at any status.
    actions.push({
        key: "duplicate",
        label: "Duplicate",
        icon: <Copy size={16} />,
        onClick: handleDuplicate,
    });

    // Delete: DRAFT only (PO-18 lifecycle — SENT/PAID are protected).
    if (status === "draft") {
        actions.push({
            key: "delete",
            label: "Delete",
            icon: <Trash size={16} />,
            danger: true,
            onClick: handleDelete,
        });
    }

    const statusLabel = po.status.charAt(0).toUpperCase() + po.status.slice(1);

    return (
        <div className="flex flex-col gap-6 font-sans pb-10">
            {/* Header: back + tabs + document actions */}
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
                    {/* Document-level downloads (not per row). */}
                    <Button
                        variant="outline-secondary"
                        onClick={handleDownloadOriginal}
                        disabled={isDownloadingOriginal}
                    >
                        <FileText size={18} /> {isDownloadingOriginal ? "Preparing..." : "Original PO PDF"}
                    </Button>
                    <Button
                        variant="outline-secondary"
                        onClick={handleDownloadCurrent}
                        disabled={isDownloadingCurrent}
                    >
                        <Download size={18} /> {isDownloadingCurrent ? "Preparing..." : "Current PDF"}
                    </Button>
                    <Dropdown
                        items={actions}
                        className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
                    />
                </div>
            </div>

            {/* Error banner */}
            {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                    {error}
                </div>
            )}

            {/* Overview tab: descriptive header + totals (left), payments (right). */}
            {activeTab === "overview" && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                    {/* LEFT: descriptive summary + running totals. */}
                    <div className="rounded-2xl border border-gray-200 bg-white p-6 flex flex-col gap-6">
                        <div className="flex items-start justify-between gap-4">
                            <div className="min-w-0">
                                <p className="text-[14px] text-gray-500">Vendor</p>
                                <p className="text-[20px] font-bold text-gray-800 truncate">
                                    {po.vendor?.vendor_name ?? po.vendor_id}
                                </p>
                            </div>
                            <Badge variant={status as BadgeVariant}>{statusLabel}</Badge>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-4">
                            <OverviewField label="Reference" value={po.po_reference} />
                            <OverviewField label="Currency" value={po.currency ?? "-"} />
                            <OverviewField
                                label="Order Date"
                                value={po.order_date ? formatDisplayDate(po.order_date) : "-"}
                            />
                            <OverviewField
                                label="Delivery Date"
                                value={po.delivery_date ? formatDisplayDate(po.delivery_date) : "-"}
                            />
                            {po.compliance_ref && (
                                <OverviewField label="Compliance Ref" value={po.compliance_ref} />
                            )}
                            <OverviewField label="Recurring" value={po.is_recurring ? "Yes" : "No"} />
                        </div>



                        <Divider />

                        {/* Totals: Total / Amount Paid / Balance Due. */}
                        <div className="w-full flex justify-end">
                            <div className="min-w-72 flex flex-col gap-3 text-gray-800">
                                <div className="flex justify-between items-center font-bold">
                                    <span>Total</span>
                                    <span>{formatCurrency(total, currency)}</span>
                                </div>
                                <div className="flex justify-between items-center">
                                    <span>Amount Paid</span>
                                    <span>{formatCurrency(amountPaid, currency)}</span>
                                </div>
                                <Divider />
                                <div className="flex justify-between items-center font-bold text-gray-900">
                                    <span>Balance Due</span>
                                    <span>{formatCurrency(balanceDue, currency)}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* RIGHT: recorded payments (read-only, no per-row download). */}
                    <div className="lg:col-span-2 rounded-2xl border border-gray-200 bg-white p-6 flex flex-col gap-4">
                        <h3 className="text-[18px] font-bold text-gray-800">Payments</h3>

                        {payments.length === 0 ? (
                            <div className="py-8 text-center text-gray-400 border-2 border-dashed border-gray-200 rounded-xl">
                                No payments recorded yet.
                            </div>
                        ) : (
                            <div className="flex flex-col gap-3">
                                {payments.map((payment) => (
                                    <Card
                                        key={payment.id}
                                        className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3"
                                    >
                                        <div className="flex items-center justify-between gap-3">
                                            <div className="min-w-0">
                                                <p className="font-bold text-gray-800">
                                                    {formatCurrency(Number(payment.amount), currency)}
                                                </p>
                                                <p className="text-[14px] text-gray-500">
                                                    {payment.payment_date
                                                        ? formatDisplayDate(payment.payment_date)
                                                        : "-"}
                                                </p>
                                            </div>
                                            {payment.reference && (
                                                <span
                                                    className="text-[14px] text-gray-500 truncate max-w-[40%]"
                                                    title={payment.reference}
                                                >
                                                    {payment.reference}
                                                </span>
                                            )}
                                        </div>
                                        {payment.notes && (
                                            <p className="text-[14px] text-gray-600 mt-2 whitespace-pre-wrap">
                                                {payment.notes}
                                            </p>
                                        )}
                                    </Card>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Detail tab: the existing PO viewer verbatim (no behavioural change). */}
            {activeTab === "detail" && (
                <PurchaseOrderViewer
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

            {/* Documents Section */}
            <div className="flex flex-col gap-2.5">
                <div className="flex items-center justify-between py-4">
                    <h3 className="text-xl font-bold text-gray-800">Documents</h3>
                    <Button
                        variant="outline"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={isUploading}
                        className="flex items-center gap-2"
                    >
                        <Plus size={16} /> {isUploading ? "Uploading..." : "Attach Document"}
                    </Button>
                    <input
                        type="file"
                        ref={fileInputRef}
                        className="hidden"
                        onChange={handleFileUpload}
                        accept={ACCEPTED_UPLOAD_TYPES}
                    />
                </div>

                <div className="flex flex-col w-full sm:flex sm:items-center sm:justify-between gap-4">
                    {po.documents?.length === 0 && !isUploading && (
                        <div className="col-span-full w-full py-4 text-center text-gray-400 border-2 border-dashed border-gray-200 rounded-xl">
                            No documents attached yet.
                        </div>
                    )}
                    {po.documents?.map((doc) => (
                        <div key={doc.id} className="flex items-center justify-between w-full py-4 transition-colors">
                            <div className="flex items-center gap-3 overflow-hidden">
                                <PaperclipIcon size={24} className="text-gray-700" />
                                <div className="min-w-0">
                                    <p className="text-gray-800 text-[16px] truncate" title={doc.filename}>{doc.filename}</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-6">
                                <p className="text-[16px] text-gray-500">{doc.file_size_kb.toFixed(1)} KB</p>
                                <Button onClick={() => handleFileDownload(doc.id, doc.filename)} aria-label="Download document"
                                    className="p-0 border-0 shadow-none bg-transparent flex items-center gap-2 text-priori-purple hover:text-priori-purple hover:bg-transparent">
                                    <Download size={24} /> <span className="text-[16px] text-priori-purple">Download</span>
                                </Button>
                                <Button onClick={() => handleFileDelete(doc.id, doc.filename)} aria-label="Delete document"
                                    className="p-0 border-0 shadow-none bg-transparent flex items-center gap-2 text-gray-600 hover:text-priori-purple hover:bg-transparent">
                                    <X size={24} /> <span className="text-[16px] text-gray-800">Delete</span>
                                </Button>
                            </div>
                        </div>
                    ))}
                    {isUploading && (
                        <div className="flex items-center justify-center p-4 border border-gray-200 rounded-xl bg-gray-50 opacity-50">
                            <div className="flex items-center gap-2 text-priori-purple">
                                <div className="w-4 h-4 border-2 border-priori-purple border-t-transparent rounded-full animate-spin"></div>
                                <span className="text-sm font-medium">Uploading...</span>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Record payment modal (PO-19): reuses the shared transactional modal. */}
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
        <div className="min-w-0">
            <p className="text-[14px] text-gray-500">{label}</p>
            <p className="text-[16px] font-bold text-gray-800 truncate">{value}</p>
        </div>
    );
}
