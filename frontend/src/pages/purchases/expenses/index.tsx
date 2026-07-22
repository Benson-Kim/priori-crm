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
import { useReportingDate } from "@/hooks/useReportingDate";
import { formatCurrency, formatDisplayDate, saveBlob } from "@/lib/utils";
import {
    deleteExpense,
    exportExpensesExcel,
    getExpenseCounts,
    getExpenses,
    type ExpenseStatusCounts,
    type ExpenseSummary,
    type PaginatedExpenses,
} from "@/services/expenseApi";
import { CheckCircle, CreditCard, Download, Eye, Plus, Trash } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function ExpensesPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);
    const [expenses, setExpenses] = useState<ExpenseSummary[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<ExpenseStatusCounts>({ all: 0, pending: 0, paid: 0, overdue: 0, canceled: 0 });
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Modal state for Record Payment
    const [isPaymentModalOpen, setIsPaymentModalOpen] = useState(false);
    const [selectedExpense, setSelectedExpense] = useState<ExpenseSummary | null>(null);
    const [paymentModalPrefillAmount, setPaymentModalPrefillAmount] = useState<number | undefined>(undefined);

    const { showConfirm, ConfirmDialog } = useConfirm();
    const navigate = useNavigate();

    const fetchExpenses = useCallback(async () => {
        setIsLoading(true);
        try {
            const data: PaginatedExpenses = await getExpenses({
                page: currentPage,
                per_page: perPage,
                status: activeTab !== "all" ? activeTab : undefined,
                search: search || undefined,
            });
            setExpenses(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load expenses");
            console.error("Failed to fetch expenses:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getExpenseCounts();
            setCounts(data);
        } catch (err) {
            console.error("Failed to fetch counts:", err);
        }
    }, []);

    const refreshAll = useCallback(() => {
        fetchExpenses();
        fetchCounts();
    }, [fetchExpenses, fetchCounts]);

    useEffect(() => {
        void (async () => { await fetchExpenses(); })();
    }, [fetchExpenses]);

    useEffect(() => {
        void (async () => { await fetchCounts(); })();
    }, [fetchCounts]);

    useEffect(() => {
        startTransition(() => { setCurrentPage(1); });
    }, [activeTab, search]);

    const handleView = (expense: ExpenseSummary) => {
        navigate(`/expenses/${expense.id}`);
    };

    const handleDelete = (expense: ExpenseSummary) => {
        showConfirm({
            title: "Delete Item?",
            description: "Are you sure you want to delete this item? This action cannot be undone.",
            confirmLabel: "Yes, delete item",
            variant: "danger",
            onConfirm: async () => {
                try {
                    await deleteExpense(expense.id);
                    refreshAll();
                } catch (err: unknown) {
                    const msg = err instanceof Error ? err.message : String(err);
                    setError(msg);
                }
            }
        });
    };

    const handleRecordPayment = (expense: ExpenseSummary) => {
        setSelectedExpense(expense);
        setPaymentModalPrefillAmount(undefined);
        setIsPaymentModalOpen(true);
    };

    const handleMarkAsPaid = (expense: ExpenseSummary) => {
        setSelectedExpense(expense);
        setPaymentModalPrefillAmount(Number(expense.balance_due));
        setIsPaymentModalOpen(true);
    };

    const [isExporting, setIsExporting] = useState(false);
    const reportingDate = useReportingDate();
    const handleExport = async () => {
        setIsExporting(true);
        try {
            const { blob, filename } = await exportExpensesExcel({
                status: activeTab !== "all" ? activeTab : undefined,
                reportingDate,
            });
            saveBlob(blob, filename || `Expenses_${new Date().toISOString().split("T")[0]}.xlsx`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to export expenses");
        } finally {
            setIsExporting(false);
        }
    };

    const getActions = (expense: ExpenseSummary): DropdownItem[] => {
        const actions: DropdownItem[] = [
            {
                key: "view",
                label: "View",
                icon: <Eye size={16} />,
                onClick: () => handleView(expense),
            },
        ];

        if (expense.status !== "paid") {
            actions.push({
                key: "mark-as-paid",
                label: "Mark as paid",
                icon: <CheckCircle size={16} />,
                onClick: () => handleMarkAsPaid(expense),
            });
            actions.push({
                key: "record-payment",
                label: "Record payment",
                icon: <CreditCard size={16} />,
                onClick: () => handleRecordPayment(expense),
            });
        }

        actions.push({
            key: "delete",
            label: "Delete",
            icon: <Trash size={16} />,
            danger: true,
            onClick: () => handleDelete(expense),
        });

        return actions;
    };

    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "pending", label: "Pending", count: counts.pending },
        { key: "paid", label: "Paid", count: counts.paid },
        { key: "overdue", label: "Overdue", count: counts.overdue },
        { key: "canceled", label: "Canceled", count: counts.canceled },
    ];

    const columns = [
        {
            key: "number",
            header: "#.",
            render: (_item: ExpenseSummary, index: number) => <span className="text-content-primary ">{(currentPage - 1) * perPage + index + 1}.</span>,
            className: "w-[50px]",
        },
        {
            key: "date",
            header: "Date",
            render: (item: ExpenseSummary) => (
                <span className="text-content-primary">
                    {formatDisplayDate(item.expense_date)}
                </span>
            ),
        },
        {
            key: "expenseRef",
            header: "Expense Ref",
            render: (item: ExpenseSummary) => (
                <span className="text-gray-500 font-medium">
                    {item.expense_reference}
                </span>
            ),
        },
        {
            key: "vendorName",
            header: "Vendor Name",
            render: (item: ExpenseSummary) => <span className="text-content-primary">{item.vendor_name}</span>,
        },
        {
            key: "amount",
            header: "Amount",
            render: (item: ExpenseSummary) => <span className="text-gray-500">{formatCurrency(Number(item.total_due), item.currency)}</span>,
        },
        {
            key: "status",
            header: "Status",
            render: (item: ExpenseSummary) => (
                <Badge variant={item.status.toLowerCase() as BadgeVariant}>
                    {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
                </Badge>
            ),
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: ExpenseSummary) => (
                <Dropdown
                    items={getActions(item)}
                />
            ),
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
                            placeholder="Search expense"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button
                            variant="primary"
                            onClick={() => navigate('/expenses/new')}>
                            <Plus size={16} /> Add Expense
                        </Button>
                    </div>
                </div>

                {/* Table */}
                <div className="overflow-x-auto rounded-b-lg border border-white">
                    {isLoading ? (
                        <LoadingState message="Loading expenses..." />
                    ) : (
                        <Table
                            columns={columns}
                            data={expenses}
                            rowKey={(item) => item.id}
                            emptyMessage="No expenses found."
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

            {/* Confirmation Dialog */}
            {ConfirmDialog}

            {/* Record Payment Modal */}
            {selectedExpense && (
                <RecordPaymentModal
                    isOpen={isPaymentModalOpen}
                    onClose={() => {
                        setIsPaymentModalOpen(false);
                        setSelectedExpense(null);
                    }}
                    entityId={selectedExpense.id}
                    entityType="expense"
                    balanceDue={Number(selectedExpense.balance_due)}
                    currency={selectedExpense.currency}
                    prefillAmount={paymentModalPrefillAmount}
                    onSuccess={() => {
                        setIsPaymentModalOpen(false);
                        setSelectedExpense(null);
                        refreshAll();
                    }}
                />
            )}
        </div>
    );
}

