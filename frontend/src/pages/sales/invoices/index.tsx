import { RecordPaymentModal } from "@/components/modals/RecordPaymentModal";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import { useConfirm } from "@/hooks/useConfirm";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    deleteInvoice,
    getInvoiceCounts,
    getInvoices,
    markAsSent,
    sendInvoice,
    type InvoiceStatusCounts,
    type InvoiceSummary,
} from "@/services/invoiceApi";
import { CheckCircle, CreditCard, Eye, Pencil, Plus, Send, Trash } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function InvoicesPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);
    const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<InvoiceStatusCounts>({
        all: 0, draft: 0, sent: 0, partial: 0, paid: 0, overdue: 0, canceled: 0,
    });
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [paymentTarget, setPaymentTarget] = useState<InvoiceSummary | null>(null);
    const { showConfirm, ConfirmDialog } = useConfirm();

    const navigate = useNavigate();

    const fetchInvoices = useCallback(async () => {
        setIsLoading(true);
        try {
            const statusMap: Record<string, string | undefined> = {
                all: undefined,
                pending: "draft",
                paid: "paid",
                overdue: "overdue",
            };
            const data = await getInvoices({
                page: currentPage,
                per_page: perPage,
                status: statusMap[activeTab],
                search: search || undefined,
            });
            setInvoices(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load expenses");
            console.error("[InvoicesPage] Failed to fetch invoices:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getInvoiceCounts();
            setCounts(data);
        } catch (err) {
            console.error("[InvoicesPage] Failed to fetch counts:", err);
            setError(err instanceof Error ? err.message : "Failed to fetch counts");
        }
    }, []);

    useEffect(() => {
        void (async () => { await fetchInvoices(); })();
    }, [fetchInvoices]);

    useEffect(() => {
        void (async () => { await fetchCounts(); })();
    }, [fetchCounts]);

    useEffect(() => {
        startTransition(() => { setCurrentPage(1); });
    }, [activeTab, search]);


    const handleSend = (invoice: InvoiceSummary) => {
        showConfirm({
            title: "Send invoice?",
            description: `Send ${invoice.invoice_number} to the customer by email? It will be marked as Sent.`,
            confirmLabel: "Yes, send",
            onConfirm: async () => {
                try {
                    await sendInvoice(invoice.id);
                    fetchInvoices();
                    fetchCounts();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to send invoice");
                }
            },
        });
    };

    const handleMarkAsSent = (invoice: InvoiceSummary) => {
        showConfirm({
            title: "Mark as sent?",
            description: "Are you sure you want to mark this invoice as sent?",
            confirmLabel: "Yes, mark as sent",
            onConfirm: async () => {
                try {
                    await markAsSent(invoice.id);
                    fetchInvoices();
                    fetchCounts();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to mark invoice as sent");
                }
            },
        });
    };

    const handleDelete = (invoice: InvoiceSummary) => {
        showConfirm({
            title: "Delete invoice?",
            description: "Are you sure you want to delete this invoice? This action cannot be undone.",
            confirmLabel: "Yes, delete it",
            variant: "danger",
            onConfirm: async () => {
                try {
                    await deleteInvoice(invoice.id);
                    fetchInvoices();
                    fetchCounts();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to delete invoice");
                }
            },
        });
    };

    const getActions = (invoice: InvoiceSummary): DropdownItem[] => {
        const actions: DropdownItem[] = [
            {
                key: "view",
                label: "View",
                icon: <Eye size={16} />,
                onClick: () => navigate(`/invoices/${invoice.id}`),
            },
        ];

        // Draft-only actions
        if (invoice.status === "draft") {
            actions.push({
                key: "edit",
                label: "Edit",
                icon: <Pencil size={16} />,
                onClick: () => navigate(`/invoices/${invoice.id}/edit`),
            });
        }

        // Send actions (draft only)
        if (invoice.status === "draft") {
            actions.push({
                key: "send",
                label: "Send",
                icon: <Send size={16} />,
                onClick: () => handleSend(invoice),
            });
            actions.push({
                key: "mark-as-sent",
                label: "Mark as sent",
                icon: <CheckCircle size={16} />,
                onClick: () => handleMarkAsSent(invoice),
            });
        }

        // Record payment: SENT/PARTIAL only, while there is still a balance
        if ((invoice.status === "sent" || invoice.status === "partial") && Number(invoice.balance_due) > 0) {
            actions.push({
                key: "record-payment",
                label: "Record payment",
                icon: <CreditCard size={16} />,
                onClick: () => setPaymentTarget(invoice),
            });
        }

        // Delete is permitted only for DRAFT
        if (invoice.status === "draft") {
            actions.push({
                key: "delete",
                label: "Delete",
                icon: <Trash size={16} />,
                danger: true,
                onClick: () => handleDelete(invoice),
            });
        }

        return actions;
    };

    const getStatusBadge = (item: InvoiceSummary) => {
        if (item.is_overdue && item.days_overdue > 0) {
            return (
                <Badge variant="overdue">Overdue ({item.days_overdue} days)</Badge>
            );
        }
        const status = item.status.toLowerCase() as
            | "paid" | "sent" | "draft" | "partial" | "canceled";
        const labelMap: Record<string, string> = {
            paid: "Paid",
            sent: "Sent",
            partial: "Partial",
            canceled: "Canceled",
            draft: "Draft",
        };
        return <Badge variant={status}>{labelMap[status] ?? item.status}</Badge>;
    };

    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "pending", label: "Pending", count: counts.draft },
        { key: "paid", label: "Paid", count: counts.paid },
        { key: "overdue", label: "Overdue", count: counts.overdue },
    ];

    const columns = [
        {
            key: "number",
            header: "#",
            render: (_item: InvoiceSummary, index: number) => (
                <span className="text-gray-600">{(currentPage - 1) * perPage + index + 1}.</span>
            ),
            className: "w-[60px]",
        },
        {
            key: "customer_name",
            header: "Customer Name",
            render: (item: InvoiceSummary) => (
                <span
                    className="font-medium text-gray-800 max-w-50 truncate block"
                    title={item.customer_name}
                >
                    {item.customer_name}
                </span>
            ),
        },
        {
            key: "invoice_number",
            header: "Invoice No.",
            render: (item: InvoiceSummary) => (
                <span
                    className="text-gray-500 max-w-37.5 truncate block"
                    title={item.invoice_number}
                >
                    {item.invoice_number}
                </span>
            ),
        },
        {
            key: "transaction_date",
            header: "Date",
            render: (item: InvoiceSummary) => (
                <span className="text-gray-800 font-medium">{formatDate(item.transaction_date)}</span>
            ),
        },
        {
            key: "total_due",
            header: "Amount",
            render: (item: InvoiceSummary) => (
                <span className="text-gray-600">{formatCurrency(Number(item.total_due), item.currency)}</span>
            ),
        },
        {
            key: "balance_due",
            header: "Balance",
            render: (item: InvoiceSummary) => (
                <span className="text-gray-600">
                    {formatCurrency(Number(item.balance_due), item.currency)}
                </span>
            ),
        },
        {
            key: "status",
            header: "Status",
            render: (item: InvoiceSummary) => getStatusBadge(item),
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: InvoiceSummary) => (
                <Dropdown items={getActions(item)} />
            ),
        },
    ];

    return (
        <div className="flex flex-col h-full space-y-6 font-sans">
            {error && (
                <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-danger">
                    {error}
                </div>
            )}

            {/* Main Table Card */}
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
                            placeholder="Search invoices"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button variant="primary" onClick={() => navigate("/invoices/add")}>
                            <Plus size={16} /> Create Invoice
                        </Button>
                    </div>
                </div>

                {/* Table Area */}
                <div className="overflow-x-auto rounded-b-lg border border-white">
                    {isLoading ? (
                        <LoadingState message="Loading invoices..." />
                    ) : (
                        <Table
                            columns={columns}
                            data={invoices}
                            rowKey={(item) => item.id}
                            emptyMessage="No invoices found."
                        />
                    )}
                </div>
            </div>

            {/* Pagination Footer */}
            <div className="py-3 px-4 border border-gray-200 mt-auto bg-white rounded-2xl ">
                <Pagination
                    currentPage={currentPage}
                    totalPages={totalPages}
                    perPage={perPage}
                    onPageChange={setCurrentPage}
                    onPerPageChange={setPerPage}
                />
            </div>

            {/* Record Payment Modal */}
            {paymentTarget && (
                <RecordPaymentModal
                    isOpen={true}
                    onClose={() => setPaymentTarget(null)}
                    entityId={paymentTarget.id}
                    entityType="invoice"
                    reference={paymentTarget.invoice_number}
                    balanceDue={Number(paymentTarget.balance_due)}
                    currency={paymentTarget.currency}
                    onPaymentRecorded={() => {
                        setPaymentTarget(null);
                        fetchInvoices();
                        fetchCounts();
                    }}
                />
            )}

            {/* Confirm Dialog */}
            {ConfirmDialog}
        </div>
    );
}
