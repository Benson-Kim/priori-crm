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
import { formatCurrency, formatDate, saveBlob } from "@/lib/utils";
import {
    approveQuote,
    convertQuoteToInvoice,
    deleteQuote,
    downloadQuotePdf,
    getQuoteCounts,
    getQuotes,
    type QuoteStatusCounts,
    type QuoteSummary
} from "@/services/quoteApi";
import { CheckCircle, Download, Eye, FileText, Plus, Send, Trash } from "lucide-react";
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
    const [error, setError] = useState<string | null>(null);
    const { showConfirm, ConfirmDialog } = useConfirm();

    const navigate = useNavigate();

    // Data fetching 
    const fetchQuotes = useCallback(async () => {
        setIsLoading(true);
        setError(null);
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
            setError(
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
        } catch (err) {
            console.error("[QuotesPage] Failed to fetch counts:", err);
            setError(err instanceof Error ? err.message : "Failed to load quotes");
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
    const handleConvertToInvoice = (quote: QuoteSummary) => {
        showConfirm({
            title: "Convert to Invoice",
            description: `Convert quote ${quote.quote_number} to an invoice? This will create a new invoice with the same details.`,
            confirmLabel: "Convert",
            onConfirm: async () => {
                try {
                    const result = await convertQuoteToInvoice(quote.id);
                    // Navigate to the new invoice detail page
                    navigate(`/invoices/${result.invoice_id}`);
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to convert quote to invoice");
                }
            },
        });
    };

    const handleDelete = (quote: QuoteSummary) => {
        showConfirm({
            title: "Delete Quote",
            description: `Permanently delete quote ${quote.quote_number}? This action cannot be undone.`,
            confirmLabel: "Delete",
            variant: "danger",
            onConfirm: async () => {
                try {
                    await deleteQuote(quote.id);
                    refreshAll();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to delete invoice");
                }
            },
        });
    };

    const handleApprove = (quote: QuoteSummary) => {
        showConfirm({
            title: "Approve Quote",
            description: `Approve quote ${quote.quote_number}?`,
            confirmLabel: "Approve",
            onConfirm: async () => {
                try {
                    await approveQuote(quote.id);
                    refreshAll();
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to approve quote");
                }
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

        // Send action - available for draft/sent quotes that aren't expired
        if (["draft", "sent"].includes(status) && !quote.is_expired) {
            actions.push({
                key: "send",
                label: "Send",
                icon: <Send size={16} />,
                onClick: () => navigate(`/quotes/${quote.id}`), // Navigate to detail page for sending
            });
        }

        // Download PDF - available for all quotes
        actions.push({
            key: "download-pdf",
            label: "Download PDF",
            icon: <Download size={16} />,
            onClick: async () => {
                try {
                    const blob = await downloadQuotePdf(quote.id);
                    saveBlob(blob, `Quote_${quote.quote_number}.pdf`);
                } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed to download PDF");
                }
            },
        });

        // Approve action - available for draft/sent quotes that aren't expired
        if (["draft", "sent"].includes(status) && !quote.is_expired) {
            actions.push({
                key: "approve",
                label: "Approve",
                icon: <CheckCircle size={16} />,
                onClick: () => handleApprove(quote),
            });
        }

        // Convert to Invoice - available for approved or sent, non-expired quotes
        if (["approved", "sent"].includes(status) && !quote.is_expired) {
            actions.push({
                key: "convert-to-invoice",
                label: "Convert to Invoice",
                icon: <FileText size={16} />,
                onClick: () => handleConvertToInvoice(quote),
            });
        }

        // Delete - only for draft quotes
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
                <span className="font-medium text-gray-800 max-w-50 truncate block">
                    {item.customer_name}
                </span>
            ),
        },
        {
            key: "quote_number",
            header: "Quote ID",
            render: (item: QuoteSummary) => (
                <span className="text-gray-500 max-w-37.5 truncate block">
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
                    {formatCurrency(Number(item.total_due), item.currency)}
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

            {error && (
                <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-danger">
                    {error}
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