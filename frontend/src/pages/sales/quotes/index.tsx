/**
 * QuotesPage — paginated, filterable quote list.
 * Mirrors InvoicesPage structure exactly.
 */

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
    approveQuote,
    deleteQuote,
    duplicateQuote,
    getQuoteCounts,
    getQuotes,
    type QuoteStatusCounts,
    type QuoteSummary,
} from "@/services/quoteApi";
import { CheckCircle, Copy, Download, Eye, Plus, Trash } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const STATUS_MAP: Record<string, string | undefined> = {
    all: undefined,
    draft: "draft",
    sent: "sent",
    approved: "approved",
    invoiced: "invoiced",
    expired: "expired",
};

export default function QuotesPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);

    const [quotes, setQuotes] = useState<QuoteSummary[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<QuoteStatusCounts>({
        all: 0, draft: 0, sent: 0, approved: 0, invoiced: 0, expired: 0,
    });
    const [isLoading, setIsLoading] = useState(true);
    const [listError, setListError] = useState<string | null>(null);
    const { showConfirm, ConfirmDialog } = useConfirm();

    const navigate = useNavigate();

    // Data fetching 
    const fetchQuotes = useCallback(async () => {
        setIsLoading(true);
        setListError(null);
        try {
            const data = await getQuotes({
                page: currentPage,
                per_page: perPage,
                status: STATUS_MAP[activeTab],
                search: search || undefined,
            });
            setQuotes(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            setListError(
                err instanceof Error ? err.message : "Failed to load quotes"
            );
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getQuoteCounts();
            setCounts(data);
        } catch {
            // Non-critical — silently ignore
        }
    }, []);

    const refreshAll = useCallback(() => {
        fetchQuotes();
        fetchCounts();
    }, [fetchQuotes, fetchCounts]);

    useEffect(() => {
        void (async () => { await fetchQuotes(); })();
    }, [fetchQuotes]);

    useEffect(() => {
        void (async () => { await fetchCounts(); })();
    }, [fetchCounts]);

    useEffect(() => {
        startTransition(() => { setCurrentPage(1); });
    }, [activeTab, search]);

    // Row actions 
    const handleApprove = (quote: QuoteSummary) => {
        showConfirm({
            title: "Approve Quote",
            description: `Approve quote ${quote.quote_number}? This will mark it as approved.`,
            confirmLabel: "Approve",
            onConfirm: async () => {
                await approveQuote(quote.id);
                refreshAll();
            },
        });
    };

    const handleDuplicate = async (quote: QuoteSummary) => {
        try {
            const dup = await duplicateQuote(quote.id);
            navigate(`/quotes/${dup.new_quote_id}/edit`);
        } catch (err) {
            setListError(
                err instanceof Error ? err.message : "Failed to duplicate quote"
            );
        }
    };

    const handleDelete = (quote: QuoteSummary) => {
        showConfirm({
            title: "Delete Quote",
            description: `Permanently delete quote ${quote.quote_number}? This action cannot be undone.`,
            confirmLabel: "Delete",
            variant: "danger",
            onConfirm: async () => {
                await deleteQuote(quote.id);
                refreshAll();
            },
        });
    };

    const getActions = (quote: QuoteSummary): DropdownItem[] => {
        const status = quote.status.toLowerCase();
        const actions: DropdownItem[] = [
            {
                key: "view",
                label: "View",
                icon: <Eye size={16} />,
                onClick: () => navigate(`/quotes/${quote.id}`),
            },
        ];

        if (["draft", "sent"].includes(status) && !quote.is_expired) {
            actions.push({
                key: "approve",
                label: "Approve",
                icon: <CheckCircle size={16} />,
                onClick: () => handleApprove(quote),
            });
        }

        actions.push({
            key: "duplicate",
            label: "Duplicate",
            icon: <Copy size={16} />,
            onClick: () => handleDuplicate(quote),
        });

        if (status === "draft") {
            actions.push({
                key: "delete",
                label: "Delete",
                icon: <Trash size={16} />,
                danger: true,
                onClick: () => handleDelete(quote),
            });
        }

        return actions;
    };

    // Status badge 
    const getStatusBadge = (item: QuoteSummary) => {
        if (item.is_expired) {
            return <Badge variant="expired">Expired</Badge>;
        }
        const status = item.status.toLowerCase() as
            | "draft" | "sent" | "approved" | "invoiced" | "expired";
        const label: Record<string, string> = {
            draft: "Draft", sent: "Sent", approved: "Approved",
            invoiced: "Invoiced", expired: "Expired",
        };
        return <Badge variant={status}>{label[status] ?? item.status}</Badge>;
    };

    // Tab config 
    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "invoiced", label: "Invoiced", count: counts.invoiced },
        { key: "draft", label: "Draft", count: counts.draft },
        { key: "sent", label: "Sent", count: counts.sent },
        { key: "approved", label: "Approved", count: counts.approved },
    ];

    // Column definitions 
    const columns = [
        {
            key: "number",
            header: "#",
            className: "w-[50px]",
            render: (_: QuoteSummary, index: number) => (
                <span className="text-gray-600">
                    {(currentPage - 1) * perPage + index + 1}.
                </span>
            ),
        },
        {
            key: "customer_name",
            header: "Customer Name",
            render: (item: QuoteSummary) => (
                <span className="font-medium text-gray-800 max-w-[200px] truncate block">
                    {item.customer_name}
                </span>
            ),
        },
        {
            key: "quote_number",
            header: "Quote ID",
            render: (item: QuoteSummary) => (
                <span className="text-gray-500 max-w-[150px] truncate block">
                    {item.quote_number}
                </span>
            ),
        },
        {
            key: "transaction_date",
            header: "Date",
            render: (item: QuoteSummary) => (
                <span className="text-gray-800 font-medium">
                    {formatDate(item.transaction_date)}
                </span>
            ),
        },
        {
            key: "total_due",
            header: "Amount",
            render: (item: QuoteSummary) => (
                <span className="text-gray-600">
                    {formatCurrency(item.total_due, item.currency)}
                </span>
            ),
        },
        {
            key: "status",
            header: "Status",
            render: (item: QuoteSummary) => getStatusBadge(item),
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: QuoteSummary) => (
                <Dropdown items={getActions(item)} />
            ),
        },
    ];

    // Render 
    return (
        <div className="flex flex-col h-full space-y-4 font-sans">

            {/* Export */}
            <div className="flex justify-end">
                <Button variant="outline-secondary">
                    <Download size={20} /> Export Excel
                </Button>
            </div>

            {/* Error Banner */}
            {listError && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                    {listError}
                </div>
            )}

            {/* Table Card */}
            <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
                <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
                    <FilterTabs
                        tabs={TABS}
                        activeTab={activeTab}
                        onTabChange={setActiveTab}
                        className="gap-3"
                    />
                    <div className="flex items-center gap-4 w-full xl:w-auto">
                        <SearchInput
                            placeholder="Search quotes"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button
                            variant="primary"
                            onClick={() => navigate("/quotes/add")}
                        >
                            <Plus size={16} /> Add Quote
                        </Button>
                    </div>
                </div>

                <div className="overflow-x-auto rounded-b-lg">
                    {isLoading ? (
                        <LoadingState message="Loading quotes..." />
                    ) : (
                        <Table
                            columns={columns}
                            data={quotes}
                            rowKey={(item) => item.id}
                            emptyMessage="No quotes found."
                        />
                    )}
                </div>
            </div>

            {/* Pagination */}
            <div className="py-3 px-4 border border-gray-200 bg-white rounded-2xl">
                <Pagination
                    currentPage={currentPage}
                    totalPages={totalPages}
                    perPage={perPage}
                    onPageChange={setCurrentPage}
                    onPerPageChange={setPerPage}
                />
            </div>

            {/* Confirm Dialog */}
            {ConfirmDialog}
        </div>
    );
}