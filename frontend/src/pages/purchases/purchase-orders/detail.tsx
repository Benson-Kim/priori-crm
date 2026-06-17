import { PurchaseOrderViewer } from "@/components/documents/PurchaseOrderViewer";
import { useHeaderOverride } from "@/components/layout/header-context";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { useConfirm } from "@/hooks/useConfirm";
import { ACCEPTED_UPLOAD_TYPES } from "@/lib/constants";
import { saveBlob } from "@/lib/utils";
import type {
    PurchaseOrderLineItem,
    PurchaseOrderResponse,
} from "@/services/purchaseOrderApi";
import {
    cancelPurchaseOrder,
    deletePurchaseOrder,
    deletePurchaseOrderDocument,
    downloadPurchaseOrderDocument,
    downloadPurchaseOrderPdf,
    duplicatePurchaseOrder,
    getPurchaseOrder,
    sendPurchaseOrder,
    uploadPurchaseOrderDocument,
} from "@/services/purchaseOrderApi";
import { ArrowLeft, Ban, Copy, Download, FileText, PaperclipIcon, Pencil, Plus, Send, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

export default function PurchaseOrderDetailPage() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();

    const [po, setPo] = useState<PurchaseOrderResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const { showConfirm, ConfirmDialog } = useConfirm();

    // Document upload state
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);
    const [isDownloadingPdf, setIsDownloadingPdf] = useState(false);

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
                await sendPurchaseOrder(po.id);
                fetchPurchaseOrder();
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

    const handleCancel = () => {
        if (!po) return;
        showConfirm({
            title: "Cancel purchase order?",
            description: `Cancel ${po.po_reference}? This voids the purchase order; it cannot be edited or sent afterwards.`,
            confirmLabel: "Yes, cancel it",
            variant: "danger",
            onConfirm: async () => {
                await cancelPurchaseOrder(po.id);
                fetchPurchaseOrder();
            },
        });
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

    const handleDownloadPdf = async () => {
        if (!po) return;
        setIsDownloadingPdf(true);
        try {
            const blob = await downloadPurchaseOrderPdf(po.id);
            saveBlob(blob, `PurchaseOrder_${po.po_reference}.pdf`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to download PDF");
        } finally {
            setIsDownloadingPdf(false);
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
    const actions: DropdownItem[] = [];

    // Draft-only: editable and sendable.
    if (po.is_editable) {
        actions.push({
            key: "edit",
            label: "Edit",
            icon: <Pencil size={16} />,
            onClick: () => navigate(`/purchase-orders/${po.id}/edit`),
        });
    }
    if (status === "draft") {
        actions.push({
            key: "send",
            label: "Send",
            icon: <Send size={16} />,
            onClick: handleSend,
        });
    }

    // Duplicate is available at any status.
    actions.push({
        key: "duplicate",
        label: "Duplicate",
        icon: <Copy size={16} />,
        onClick: handleDuplicate,
    });

    // Cancel: DRAFT or SENT only.
    if (status === "draft" || status === "sent") {
        actions.push({
            key: "cancel",
            label: "Cancel",
            icon: <Ban size={16} />,
            danger: true,
            onClick: handleCancel,
        });
    }

    // Delete: DRAFT or CANCELED only.
    if (status === "draft" || status === "canceled") {
        actions.push({
            key: "delete",
            label: "Delete",
            icon: <X size={16} />,
            danger: true,
            onClick: handleDelete,
        });
    }

    return (
        <div className="flex flex-col gap-6 font-sans pb-10">
            {/* Header */}
            <div className="flex items-center justify-between">
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => navigate("/purchase-orders")}
                    aria-label="Back to purchase orders"
                >
                    <ArrowLeft size={20} />
                </Button>

                <div className="flex items-center gap-3">
                    <Button
                        variant="outline-secondary"
                        onClick={handleDownloadPdf}
                        disabled={isDownloadingPdf}
                    >
                        <FileText size={18} /> {isDownloadingPdf ? "Preparing..." : "Download PDF"}
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

            {/* Document Viewer */}
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

            {/* Confirm Dialog */}
            {ConfirmDialog}
        </div>
    );
}
