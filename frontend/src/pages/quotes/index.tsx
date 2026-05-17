import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import {
    approveQuote,
    deleteQuote,
    getQuoteCounts,
    getQuotes,
    type QuoteStatusCounts,
    type QuoteSummary,
} from "@/lib/quoteApi";
import { formatCurrency, formatDate } from "@/lib/utils";
import { CheckCircle, Download, Eye, Plus, Trash } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

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
    const navigate = useNavigate();

    const fetchQuotes = useCallback(async () => {
        setIsLoading(true);
        try {
            const statusMap: Record<string, string | undefined> = {
                all: undefined,
                invoiced: "invoiced",
                draft: "draft",
                sent: "sent",
                approved: "approved",
            };
            const data = await getQuotes({
                page: currentPage,
                per_page: perPage,
                status: statusMap[activeTab],
                search: search || undefined,
            });
            setQuotes(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            console.error("[QuotesPage] Failed to fetch quotes:", err);
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
        }
    }, []);

    useEffect(() => { fetchQuotes(); }, [fetchQuotes]);
    useEffect(() => { fetchCounts(); }, [fetchCounts]);
    useEffect(() => { setCurrentPage(1); }, [activeTab, search]);

    const handleApprove = async (quote: QuoteSummary) => {
        try {
            await approveQuote(quote.id);
            fetchQuotes();
            fetchCounts();
        } catch (err) {
            console.error("[QuotesPage] Approve failed:", err);
        }
    };

    const handleDelete = async (quote: QuoteSummary) => {
        if (!confirm("Are you sure you want to delete this quote?")) return;
        try {
            await deleteQuote(quote.id);
            fetchQuotes();
            fetchCounts();
        } catch (err) {
            console.error("[QuotesPage] Delete failed:", err);
        }
    };

    const getActions = (quote: QuoteSummary): DropdownItem[] => {
        const isDraft = quote.status.toLowerCase() === "draft";
        const canApprove =
            ["sent", "draft"].includes(quote.status.toLowerCase()) && !quote.is_expired;

        const actions: DropdownItem[] = [
            {
                key: "view",
                label: "View",
                icon: <Eye size={16} />,
                onClick: () => navigate(`/quotes/${quote.id}`),
            },
        ];

        if (canApprove) {
            actions.push({
                key: "approve",
                label: "Approve",
                icon: <CheckCircle size={16} />,
                onClick: () => handleApprove(quote),
            });
        }

        if (isDraft) {
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

    const getStatusBadge = (item: QuoteSummary) => {
        if (item.is_expired) {
            return <Badge variant="expired">Expired</Badge>;
        }
        const status = item.status.toLowerCase() as
            | "invoiced" | "approved" | "sent" | "draft";
        const labelMap: Record<string, string> = {
            invoiced: "Invoiced",
            approved: "Approved",
            sent: "Sent",
            draft: "Draft",
        };
        return <Badge variant={status}>{labelMap[status] ?? item.status}</Badge>;
    };

    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "invoiced", label: "Invoiced", count: counts.invoiced },
        { key: "draft", label: "Draft", count: counts.draft },
        { key: "sent", label: "Sent", count: counts.sent },
        { key: "approved", label: "Approved", count: counts.approved },
    ];

    const columns = [
        {
            key: "number",
            header: "#",
            render: (_item: QuoteSummary, index: number) => (
                <span className="text-gray-600">{(currentPage - 1) * perPage + index + 1}.</span>
            ),
            className: "w-[60px]",
        },
        {
            key: "customer_name",
            header: "Customer Name",
            render: (item: QuoteSummary) => (
                <span
                    className="font-medium text-gray-800 max-w-[200px] truncate block"
                    title={item.customer_name}
                >
                    {item.customer_name}
                </span>
            ),
        },
        {
            key: "quote_number",
            header: "Quote ID",
            render: (item: QuoteSummary) => (
                <span
                    className="text-gray-500 max-w-[150px] truncate block"
                    title={item.quote_number}
                >
                    {item.quote_number}
                </span>
            ),
        },
        {
            key: "transaction_date",
            header: "Date",
            render: (item: QuoteSummary) => (
                <span className="text-gray-800 font-medium">{formatDate(item.transaction_date)}</span>
            ),
        },
        {
            key: "total_due",
            header: "Amount",
            render: (item: QuoteSummary) => (
                <span className="text-gray-600">{formatCurrency(item.total_due, item.currency)}</span>
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

    return (
        <div className="flex flex-col h-full space-y-6 font-sans">

            {/* Top Action Bar */}
            <div className="flex justify-end mt-4">
                <Button variant="outline-secondary">
                    <Download size={20} /> Export Excel
                </Button>
            </div>

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
                            placeholder="Search quotes"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button variant="primary" onClick={() => navigate("/quotes/add")}>
                            <Plus size={16} /> Add Quote
                        </Button>
                    </div>
                </div>

                {/* Table Area */}
                <div className="overflow-x-auto rounded-b-lg border border-white">
                    {isLoading ? (
                        <div className="flex items-center justify-center h-40 text-gray-400">
                            Loading quotes...
                        </div>
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
        </div>
    );
}
