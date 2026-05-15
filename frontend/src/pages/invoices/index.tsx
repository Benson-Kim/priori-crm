import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import {
    cancelInvoice,
    duplicateInvoice,
    getInvoiceCounts,
    getInvoices,
    markAsSent,
    type InvoiceStatusCounts,
    type InvoiceSummary,
} from "@/lib/invoiceApi";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    Ban,
    CheckCircle,
    Copy,
    CreditCard,
    Download,
    Eye,
    Pencil,
    Plus,
    Send,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const STATUS_BADGE_MAP: Record<string, "draft" | "sent" | "paid" | "partial" | "overdue" | "canceled"> = {
    draft: "draft",
    sent: "sent",
    paid: "paid",
    partial: "partial",
    overdue: "overdue",
    canceled: "canceled",
};

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
    const navigate = useNavigate();

    const fetchInvoices = useCallback(async () => {
        setIsLoading(true);
        try {
            // Map tab keys to backend status values
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
            console.log("[InvoicesPage] Fetched invoices:", data);
            setInvoices(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            console.error("[InvoicesPage] Failed to fetch invoices:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getInvoiceCounts();
            console.log("[InvoicesPage] Fetched counts:", data);
            setCounts(data);
        } catch (err) {
            console.error("[InvoicesPage] Failed to fetch counts:", err);
        }
    }, []);

    useEffect(() => { fetchInvoices(); }, [fetchInvoices]);
    useEffect(() => { fetchCounts(); }, [fetchCounts]);

    // Reset to page 1 when filters change
    useEffect(() => { setCurrentPage(1); }, [activeTab, search]);

    const handleMarkSent = async (invoice: InvoiceSummary) => {
        try {
            await markAsSent(invoice.id);
            console.log("[InvoicesPage] Marked as sent:", invoice.invoice_number);
            fetchInvoices();
            fetchCounts();
        } catch (err) {
            console.error("[InvoicesPage] Mark as sent failed:", err);
        }
    };

    const handleDuplicate = async (invoice: InvoiceSummary) => {
        try {
            const dup = await duplicateInvoice(invoice.id);
            console.log("[InvoicesPage] Duplicated:", dup.invoice_number);
            navigate(`/invoices/${dup.id}/edit`);
        } catch (err) {
            console.error("[InvoicesPage] Duplicate failed:", err);
        }
    };

    const handleCancel = async (invoice: InvoiceSummary) => {
        if (!confirm(`Cancel invoice ${invoice.invoice_reference}? This is irreversible.`)) return;
        try {
            await cancelInvoice(invoice.id);
            console.log("[InvoicesPage] Cancelled:", invoice.invoice_number);
            fetchInvoices();
            fetchCounts();
        } catch (err) {
            console.error("[InvoicesPage] Cancel failed:", err);
        }
    };

    const getActions = (item: InvoiceSummary) => {
        const status = item.status.toLowerCase();
        const actions = [];

        // View — always
        actions.push({
            key: "view",
            label: "View",
            icon: <Eye size={16} />,
            onClick: () => navigate(`/invoices/${item.id}`),
        });

        // Edit — draft or sent
        if (status === "draft" || status === "sent") {
            actions.push({
                key: "edit",
                label: "Edit",
                icon: <Pencil size={16} />,
                onClick: () => navigate(`/invoices/${item.id}/edit`),
            });
        }

        // Mark as Sent — draft only
        if (status === "draft") {
            actions.push({
                key: "mark-sent",
                label: "Mark as Sent",
                icon: <CheckCircle size={16} />,
                onClick: () => handleMarkSent(item),
            });
        }

        // Send — draft or sent (shows coming soon)
        if (status === "draft" || status === "sent") {
            actions.push({
                key: "send",
                label: "Send",
                icon: <Send size={16} />,
                onClick: () => alert("Email sending — coming soon"),
            });
        }

        // Record Payment — sent, overdue, partial, paid
        if (["sent", "overdue", "partial", "paid"].includes(status)) {
            actions.push({
                key: "record-payment",
                label: "Record Payment",
                icon: <CreditCard size={16} />,
                onClick: () => navigate(`/invoices/${item.id}?action=record-payment`),
            });
        }

        // Download PDF — always (stub)
        actions.push({
            key: "download-pdf",
            label: "Download PDF",
            icon: <Download size={16} />,
            onClick: () => alert("PDF generation — coming soon"),
        });

        // Duplicate — always
        actions.push({
            key: "duplicate",
            label: "Duplicate",
            icon: <Copy size={16} />,
            onClick: () => handleDuplicate(item),
        });

        // Cancel — not already canceled
        if (status !== "canceled") {
            actions.push({
                key: "cancel",
                label: "Cancel Invoice",
                icon: <Ban size={16} />,
                onClick: () => handleCancel(item),
            });
        }

        return actions;
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
                <span className="text-content-primary">{(currentPage - 1) * perPage + index + 1}.</span>
            ),
            className: "w-[50px]",
        },
        {
            key: "customer",
            header: "Customer Name",
            render: (item: InvoiceSummary) => (
                <button
                    type="button"
                    className="text-content-primary hover:text-priori-purple font-medium text-left"
                    onClick={() => navigate(`/customers/${item.customer_id}`)}
                >
                    {item.customer_name}
                </button>
            ),
        },
        {
            key: "invoiceNo",
            header: "Invoice No.",
            render: (item: InvoiceSummary) => (
                <button
                    type="button"
                    className="text-priori-purple hover:underline text-left"
                    onClick={() => navigate(`/invoices/${item.id}`)}
                >
                    {item.invoice_number}
                </button>
            ),
        },
        {
            key: "date",
            header: "Date",
            render: (item: InvoiceSummary) => (
                <span className="text-gray-500">{formatDate(item.transaction_date)}</span>
            ),
        },
        {
            key: "amount",
            header: "Amount",
            render: (item: InvoiceSummary) => (
                <span className="text-gray-700 font-medium">
                    {formatCurrency(item.total_due, item.currency === "KES" ? "KES" : item.currency)}
                </span>
            ),
        },
        {
            key: "status",
            header: "Status",
            render: (item: InvoiceSummary) => {
                const status = item.status.toLowerCase();
                const variant = STATUS_BADGE_MAP[status] || "draft";
                const label = status.charAt(0).toUpperCase() + status.slice(1);
                const overdueText = item.is_overdue && item.days_overdue > 0
                    ? `Overdue (${item.days_overdue} days)`
                    : label;
                return <Badge variant={variant}>{item.is_overdue ? overdueText : label}</Badge>;
            },
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: InvoiceSummary) => (
                <Dropdown items={getActions(item)} className="" />
            ),
        },
    ];

    return (
        <div className="flex flex-col h-full space-y-4">
            <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col min-h-0 flex-1">
                {/* Actions Bar */}
                <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
                    <FilterTabs
                        tabs={TABS}
                        activeTab={activeTab}
                        onTabChange={setActiveTab}
                    />
                    <div className="flex items-center gap-6 w-full sm:w-auto">
                        <SearchInput
                            placeholder="Search Invoices"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button
                            onClick={() => navigate("/invoices/add")}
                            className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg font-normal whitespace-nowrap px-4 py-3 flex items-center gap-2"
                        >
                            <Plus size={14} />
                            Add Invoice
                        </Button>
                    </div>
                </div>

                {/* Table */}
                <div className="flex-1 overflow-auto min-h-0 rounded-b-lg">
                    {isLoading ? (
                        <div className="flex items-center justify-center h-40 text-gray-400">
                            Loading invoices...
                        </div>
                    ) : invoices.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-40 text-gray-400 gap-2">
                            <p>No invoices found.</p>
                            <Button
                                onClick={() => navigate("/invoices/add")}
                                className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg px-4 py-2 text-sm"
                            >
                                Create your first invoice
                            </Button>
                        </div>
                    ) : (
                        <Table
                            columns={columns}
                            data={invoices}
                            rowKey={(item) => item.id}
                        />
                    )}
                </div>
            </div>

            {/* Pagination */}
            <div className="py-3 px-4 border border-gray-200 mt-auto bg-white rounded-2xl">
                <Pagination
                    currentPage={currentPage}
                    totalPages={totalPages}
                    perPage={perPage}
                    onPageChange={setCurrentPage}
                    onPerPageChange={setPerPage}
                />
            </div>
        </div>
    );
}
