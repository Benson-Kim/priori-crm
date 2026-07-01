import { RecordPaymentModal } from "@/components/modals/RecordPaymentModal";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import { useConfirm } from "@/hooks/useConfirm";
import { formatCurrency, formatDisplayDate, saveBlob } from "@/lib/utils";
import {
    deletePurchaseOrder,
    duplicatePurchaseOrder,
    exportPurchaseOrdersExcel,
    getPurchaseOrderCounts,
    getPurchaseOrders,
    markAsSentPurchaseOrder,
    sendPurchaseOrder,
    type PaginatedPurchaseOrders,
    type PurchaseOrderStatusCounts,
    type PurchaseOrderSummary,
} from "@/services/purchaseOrderApi";
import { CheckCircle, Copy, CreditCard, Download, Eye, Pencil, Plus, Send, Trash } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function PurchaseOrdersPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);
    const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrderSummary[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<PurchaseOrderStatusCounts>({
        all: 0,
        draft: 0,
        sent: 0,
        paid: 0,
    });
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    // The PO selected for the Record Payment modal. The modal is row-scoped:
    // amount/balance/currency all come from this row, so it is null when the
    // modal is closed.
    const [paymentTarget, setPaymentTarget] = useState<PurchaseOrderSummary | null>(null);

    const { showConfirm, ConfirmDialog } = useConfirm();
    const navigate = useNavigate();

    const fetchPurchaseOrders = useCallback(async () => {
        setIsLoading(true);
        try {
            const data: PaginatedPurchaseOrders = await getPurchaseOrders({
                page: currentPage,
                per_page: perPage,
                status: activeTab !== "all" ? activeTab : undefined,
                search: search || undefined,
            });
            setPurchaseOrders(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load purchase orders");
            console.error("Failed to fetch purchase orders:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getPurchaseOrderCounts();
            setCounts(data);
        } catch (err) {
            console.error("Failed to fetch counts:", err);
        }
    }, []);

    const refreshAll = useCallback(() => {
        fetchPurchaseOrders();
        fetchCounts();
    }, [fetchPurchaseOrders, fetchCounts]);

    useEffect(() => {
        void (async () => { await fetchPurchaseOrders(); })();
    }, [fetchPurchaseOrders]);

    useEffect(() => {
        void (async () => { await fetchCounts(); })();
    }, [fetchCounts]);

    useEffect(() => {
        startTransition(() => { setCurrentPage(1); });
    }, [activeTab, search]);

    const handleView = (po: PurchaseOrderSummary) => {
        navigate(`/purchase-orders/${po.id}`);
    };

    const handleEdit = (po: PurchaseOrderSummary) => {
        navigate(`/purchase-orders/${po.id}/edit`);
    };

    const handleSend = (po: PurchaseOrderSummary) => {
        showConfirm({
            title: "Send purchase order?",
            description: `Send ${po.po_reference} to the vendor by email? It will be marked as Sent.`,
            confirmLabel: "Yes, send",
            onConfirm: async () => {
                try {
                    await sendPurchaseOrder(po.id);
                    refreshAll();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to send purchase order");
                }
            },
        });
    };

    const handleMarkAsSent = (po: PurchaseOrderSummary) => {
        showConfirm({
            title: "Mark as sent?",
            description: "Are you sure you want to mark this item as sent?",
            confirmLabel: "Yes, mark as sent",
            onConfirm: async () => {
                try {
                    await markAsSentPurchaseOrder(po.id);
                    refreshAll();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to mark purchase order as sent");
                }
            },
        });
    };

    const handleDuplicate = async (po: PurchaseOrderSummary) => {
        try {
            const result = await duplicatePurchaseOrder(po.id);
            // Redirect straight to the editable copy (mirrors the quote flow).
            navigate(`/purchase-orders/${result.new_po_id}/edit`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to duplicate purchase order");
        }
    };

    const handleDelete = (po: PurchaseOrderSummary) => {
        showConfirm({
            title: "Delete purchase order?",
            description: "Are you sure you want to delete this purchase order? This action cannot be undone.",
            confirmLabel: "Yes, delete it",
            variant: "danger",
            onConfirm: async () => {
                try {
                    await deletePurchaseOrder(po.id);
                    refreshAll();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to delete purchase order");
                }
            },
        });
    };

    const [isExporting, setIsExporting] = useState(false);
    const handleExport = async () => {
        setIsExporting(true);
        try {
            const blob = await exportPurchaseOrdersExcel({
                status: activeTab !== "all" ? activeTab : undefined,
                search: search || undefined,
            });
            saveBlob(blob, `PurchaseOrders_${new Date().toISOString().split("T")[0]}.xlsx`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to export purchase orders");
        } finally {
            setIsExporting(false);
        }
    };

    const getActions = (po: PurchaseOrderSummary): DropdownItem[] => {
        const actions: DropdownItem[] = [
            {
                key: "view",
                label: "View",
                icon: <Eye size={16} />,
                onClick: () => handleView(po),
            },
        ];

        // Draft-only actions: a PO is editable / sendable only while DRAFT.
        if (po.status === "draft") {
            actions.push({
                key: "edit",
                label: "Edit",
                icon: <Pencil size={16} />,
                onClick: () => handleEdit(po),
            });
        }
        if (po.status != "sent") {
            actions.push({
                key: "send",
                label: "Send",
                icon: <Send size={16} />,
                onClick: () => handleSend(po),
            });
            actions.push({
                key: "mark-as-sent",
                label: "Mark as sent",
                icon: <CheckCircle size={16} />,
                onClick: () => handleMarkAsSent(po),
            });
        }

        // Duplicate is available at any status (also the way to "re-raise").
        actions.push({
            key: "duplicate",
            label: "Duplicate",
            icon: <Copy size={16} />,
            onClick: () => handleDuplicate(po),
        });

        // Record payment: SENT only, while there is still a balance to clear.
        if (po.status === "sent" && Number(po.balance_due) > 0) {
            actions.push({
                key: "record-payment",
                label: "Record payment",
                icon: <CreditCard size={16} />,
                onClick: () => setPaymentTarget(po),
            });
        }

        // Delete is permitted only for DRAFT (SENT/PAID protected).
        if (po.status === "draft") {
            actions.push({
                key: "delete",
                label: "Delete",
                icon: <Trash size={16} />,
                danger: true,
                onClick: () => handleDelete(po),
            });
        }

        return actions;
    };

    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "draft", label: "Draft", count: counts.draft },
        { key: "sent", label: "Sent", count: counts.sent },
        { key: "paid", label: "Paid", count: counts.paid },
    ];

    const columns = [
        {
            key: "number",
            header: "#.",
            render: (_item: PurchaseOrderSummary, index: number) => (
                <span className="text-content-primary ">{(currentPage - 1) * perPage + index + 1}.</span>
            ),
            className: "w-[50px]",
        },
        {
            key: "date",
            header: "Date",
            render: (item: PurchaseOrderSummary) => (
                <span className="text-content-primary">{formatDisplayDate(item.order_date)}</span>
            ),
        },
        {
            key: "poRef",
            header: "PO Ref",
            render: (item: PurchaseOrderSummary) => (
                <span className="text-gray-500 font-medium">{item.po_reference}</span>
            ),
        },
        {
            key: "vendorName",
            header: "Vendor Name",
            render: (item: PurchaseOrderSummary) => (
                <span className="text-content-primary">{item.vendor_name}</span>
            ),
        },
        {
            key: "amount",
            header: "Amount",
            render: (item: PurchaseOrderSummary) => (
                <span className="text-gray-500">{formatCurrency(Number(item.total), item.currency)}</span>
            ),
        },
        {
            key: "balance",
            header: "Balance",
            render: (item: PurchaseOrderSummary) => (
                <span className="text-gray-500">{formatCurrency(Number(item.balance_due), item.currency)}</span>
            ),
        },
        {
            key: "status",
            header: "Status",
            render: (item: PurchaseOrderSummary) => {
                // A PAID PO whose balance has gone negative has been settled
                // beyond its total: present it as "Overpaid" (the ledger status
                // stays "paid"; this is a display-only derivation).
                const isOverpaid =
                    item.status.toLowerCase() === "paid" &&
                    Number(item.balance_due) < 0;
                const variant = (isOverpaid ? "overpaid" : item.status.toLowerCase()) as BadgeVariant;
                const label = isOverpaid
                    ? "Overpaid"
                    : item.status.charAt(0).toUpperCase() + item.status.slice(1);
                return <Badge variant={variant}>{label}</Badge>;
            },
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: PurchaseOrderSummary) => <Dropdown items={getActions(item)} />,
        },
    ];

    return (
        <div className="flex flex-col h-full space-y-4 font-sans">
            {/* Top Header */}
            <div className="flex justify-end mt-4">
                <Button
                    variant="outline-secondary"
                    onClick={handleExport}
                    disabled={isExporting}
                >
                    <Download size={20} /> {isExporting ? "Exporting..." : "Export Excel"}
                </Button>
            </div>

            {error && (
                <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
                    {error}
                </div>
            )}

            {/* Top Card / Container */}
            <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
                {/* Actions Bar */}
                <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
                    <FilterTabs
                        tabs={TABS}
                        activeTab={activeTab}
                        onTabChange={setActiveTab}
                    />

                    <div className="flex items-center gap-4 w-full xl:w-auto">
                        <SearchInput
                            placeholder="Search purchase order"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button
                            variant="primary"
                            onClick={() => navigate('/purchase-orders/new')}>
                            <Plus size={16} /> Create PO
                        </Button>
                    </div>
                </div>

                {/* Table */}
                <div className="overflow-x-auto rounded-b-lg border border-white">
                    {isLoading ? (
                        <LoadingState message="Loading purchase orders..." />
                    ) : (
                        <Table
                            columns={columns}
                            data={purchaseOrders}
                            rowKey={(item) => item.id}
                            emptyMessage="No purchase orders found."
                        />
                    )}
                </div>
            </div>

            {/* Pagination */}
            <div className="py-3 px-4 border border-gray-200 mt-auto bg-white rounded-2xl ">
                <Pagination
                    currentPage={currentPage}
                    totalPages={totalPages}
                    perPage={perPage}
                    onPageChange={setCurrentPage}
                    onPerPageChange={setPerPage}
                />
            </div>

            <RecordPaymentModal
                isOpen={paymentTarget !== null}
                onClose={() => setPaymentTarget(null)}
                entityId={paymentTarget?.id ?? ""}
                entityType="purchaseOrder"
                balanceDue={Number(paymentTarget?.balance_due ?? 0)}
                currency={paymentTarget?.currency ?? ""}
                reference={paymentTarget?.po_reference}
                onSuccess={() => {
                    setPaymentTarget(null);
                    refreshAll();
                }}
            />

            {/* Confirmation Dialog */}
            {ConfirmDialog}
        </div>
    );
}
