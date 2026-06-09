import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import { useConfirm } from "@/hooks/useConfirm";
import { formatCurrency } from "@/lib/utils";
import {
    activateCustomer,
    checkDeleteEligibility,
    deactivateCustomer,
    deleteCustomer,
    getCustomerCounts,
    getCustomers,
    type CustomerSummary,
    type DeleteCheckResult,
    type PaginatedCustomers,
    type StatusCounts,
} from "@/services/customerApi";
import { Ban, CheckCircle, Eye, Pencil, Plus, Trash } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function CustomersPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);
    const [customers, setCustomers] = useState<CustomerSummary[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<StatusCounts>({ all: 0, active: 0, inactive: 0, suspended: 0, deleted: 0 });
    const [isLoading, setIsLoading] = useState(true);
    const [, setError] = useState<string | null>(null);

    const { showConfirm, ConfirmDialog } = useConfirm();

    const navigate = useNavigate();

    // Data fetching

    const fetchCustomers = useCallback(async () => {
        setIsLoading(true);
        try {
            const data: PaginatedCustomers = await getCustomers({
                page: currentPage,
                per_page: perPage,
                status: activeTab !== "all" ? activeTab : undefined,
                search: search || undefined,
            });
            setCustomers(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load customers");
            console.error("Failed to fetch customers:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getCustomerCounts();
            setCounts(data);
        } catch (err) {
            console.error("Failed to fetch counts:", err);
        }
    }, []);

    const refreshAll = useCallback(() => {
        fetchCustomers();
        fetchCounts();
    }, [fetchCustomers, fetchCounts]);

    useEffect(() => {
        void (async () => { await fetchCustomers(); })();
    }, [fetchCustomers]);
    useEffect(() => {
        void (async () => { await fetchCounts(); })();
    }, [fetchCounts]);

    // Reset to page 1 when filters change
    useEffect(() => {
        startTransition(() => { setCurrentPage(1); });
    }, [activeTab, search]);

    // Actions

    const handleView = (customer: CustomerSummary) => {
        navigate(`/customers/${customer.id}`);
    };

    const handleEdit = (customer: CustomerSummary) => {
        navigate(`/customers/${customer.id}/edit`);
    };

    const handleActivate = (customer: CustomerSummary) => {
        if (customer.status === "active") return;
        showConfirm({
            title: "Activate Customer",
            description: `Are you sure you want to activate ${customer.display_name}?`,
            confirmLabel: "Activate",
            onConfirm: async () => {
                await activateCustomer(customer.id);
                refreshAll();
            }
        });
    };

    const handleDeactivate = (customer: CustomerSummary) => {
        if (customer.status === "inactive") return;

        const hasBalance = customer.balance > 0;

        showConfirm({
            title: "Deactivate Customer",
            description: hasBalance
                ? `${customer.display_name} has an outstanding balance of ${formatCurrency(customer.balance, customer.currency)}. Are you sure you want to force deactivation?`
                : `Are you sure you want to deactivate ${customer.display_name}?`,
            confirmLabel: hasBalance ? "Force Deactivate" : "Deactivate",
            variant: hasBalance ? "danger" : "default",
            onConfirm: async () => {
                await deactivateCustomer(customer.id, hasBalance);
                refreshAll();
            }
        });
    };

    const handleDelete = async (customer: CustomerSummary) => {
        try {
            const check: DeleteCheckResult = await checkDeleteEligibility(customer.id);

            showConfirm({
                title: check.can_hard_delete ? "Permanently Delete" : "Archive Customer",
                description: check.message,
                confirmLabel: check.can_hard_delete ? "Delete Permanently" : "Archive",
                variant: "danger",
                children: check.warnings.length > 0 && (
                    <div className="mt-2 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
                        <p className="font-semibold mb-1">Warnings:</p>
                        <ul className="list-disc list-inside">
                            {check.warnings.map((w, i) => <li key={i}>{w}</li>)}
                        </ul>
                    </div>
                ),
                onConfirm: async () => {
                    await deleteCustomer(customer.id, check.can_hard_delete);
                    refreshAll();
                }
            });
        } catch (err) {
            console.error("Failed to check delete eligibility:", err);
        }
    };


    // Action menu items (contextual per customer status)

    const getActions = (customer: CustomerSummary): DropdownItem[] => {
        const actions: DropdownItem[] = [
            {
                key: "view",
                label: "View",
                icon: <Eye size={16} />,
                onClick: () => handleView(customer),
            },
            {
                key: "edit",
                label: "Edit",
                icon: <Pencil size={16} />,
                onClick: () => handleEdit(customer),
            },
        ];

        if (customer.status !== "active" && customer.status !== "deleted") {
            actions.push({
                key: "activate",
                label: "Activate",
                icon: <CheckCircle size={16} />,
                onClick: () => handleActivate(customer),
            });
        }

        if (customer.status === "active") {
            actions.push({
                key: "deactivate",
                label: "Deactivate",
                icon: <Ban size={16} />,
                onClick: () => handleDeactivate(customer),
            });
        }

        if (customer.status !== "deleted") {
            actions.push({
                key: "delete",
                label: "Delete",
                icon: <Trash size={16} />,
                danger: true,
                onClick: () => handleDelete(customer),
            });
        }

        return actions;
    };

    // Layout

    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "active", label: "Active", count: counts.active },
        { key: "inactive", label: "Inactive", count: counts.inactive },
    ];

    const columns = [
        {
            key: "number",
            header: "#.",
            render: (_item: CustomerSummary, index: number) => <span className="text-content-primary ">{(currentPage - 1) * perPage + index + 1}.</span>,
            className: "w-[50px]",
        },
        {
            key: "name",
            header: "Customer Name",
            render: (item: CustomerSummary) => (
                <span className=" text-content-primary">
                    {item.display_name}
                </span>
            ),
        },
        {
            key: "email",
            header: "Email",
            render: (item: CustomerSummary) => <span className="text-gray-500">{item.email}</span>,
        },
        {
            key: "phone",
            header: "Phone",
            render: (item: CustomerSummary) => <span className="text-content-primary">{item.phone}</span>,
        },
        {
            key: "balance",
            header: "Balance",
            render: (item: CustomerSummary) => <span className="text-gray-500">{formatCurrency(item.balance, item.currency ?? "Ksh")}</span>,
        },
        {
            key: "status",
            header: "Status",
            render: (item: CustomerSummary) => (
                <Badge variant={item.status.toLowerCase() as "active" | "inactive"}>
                    {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
                </Badge>
            ),
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: CustomerSummary) => (
                <Dropdown
                    items={getActions(item)}
                />
            ),
        },
    ];

    return (
        <div className="flex flex-col space-y-4">
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
                            placeholder="Search customer"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button
                            variant="primary"
                            onClick={() => navigate("/customers/add")}>
                            <Plus size={16} /> Add Customer
                        </Button>
                    </div>


                </div>

                {/* Table */}
                <div className="overflow-x-auto rounded-b-lg border border-white">
                    {isLoading ? (
                        <LoadingState message="Loading customers..." />
                    ) : (
                        <Table
                            columns={columns}
                            data={customers}
                            rowKey={(item) => item.id}
                            emptyMessage="No customers found."
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
        </div>
    );
}
