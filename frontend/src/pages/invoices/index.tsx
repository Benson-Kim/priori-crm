import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import {
    getInvoiceCounts,
    getInvoices,
    type Invoice,
    type PaginatedInvoice,
    type StatusCounts,
} from "@/lib/invoiceApi";
import { formatCurrency } from "@/lib/utils";
import { CheckCircle, Eye, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function CustomersPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);
    const [invoices, setInvoices] = useState<Invoice[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<StatusCounts>({ all: 0, active: 0, inactive: 0 });
    const [isLoading, setIsLoading] = useState(true);
    const navigate = useNavigate();

    const fetchCustomers = useCallback(async () => {
        setIsLoading(true);
        try {
            const data: PaginatedInvoice = await getInvoices({
                page: currentPage,
                per_page: perPage,
                status: activeTab !== "all" ? activeTab : undefined,
                search: search || undefined,
            });
            setInvoices(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            console.error("Failed to fetch invoices:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getInvoiceCounts();
            setCounts(data);
        } catch (err) {
            console.error("Failed to fetch counts:", err);
        }
    }, []);

    useEffect(() => {
        fetchCustomers();
    }, [fetchCustomers]);

    useEffect(() => {
        fetchCounts();
    }, [fetchCounts]);

    // Reset to page 1 when filters change
    useEffect(() => {
        setCurrentPage(1);
    }, [activeTab, search]);

    const handleAction = (action: string, customer: Invoice) => {
        console.log(`${action} customer:`, customer.first_name);
    };

    const handleAddCustomer = () => {
        navigate("/invoices/add");
    };

    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "active", label: "Active", count: counts.active },
        { key: "inactive", label: "Inactive", count: counts.inactive },
    ];

    const columns = [
        {
            key: "number",
            header: "#.",
            render: (_item: Invoice, index: number) => <span className="text-content-primary ">{(currentPage - 1) * perPage + index + 1}.</span>,
            className: "w-[50px]",
        },
        {
            key: "name",
            header: "Customer Name",
            render: (item: Invoice) => (
                <span className=" text-content-primary">
                    {item.company_name || `${item.first_name} ${item.last_name}`}
                </span>
            ),
        },
        {
            key: "invoiceNumber",
            header: "Invoice No.",
            render: (item: Invoice) => <span className="text-gray-500">{item.email}</span>,
        },
        {
            key: "amount",
            header: "Amount",
            render: (item: Invoice) => <span className="text-gray-500">{formatCurrency(item.amount, 'Ksh')}</span>,
        },
        {
            key: "status",
            header: "Status",
            render: (item: Invoice) => (
                <Badge variant={item.status.toLowerCase() as "active" | "inactive"}>
                    {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
                </Badge>
            ),
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: Invoice) => (
                <Dropdown
                    items={[
                        {
                            key: "view",
                            label: "View",
                            icon: <Eye size={16} />,
                            onClick: () => handleAction("View", item),
                        },
                        {
                            key: "approve",
                            label: "Approve",
                            icon: <CheckCircle size={16} />,
                            onClick: () => handleAction("Approve", item),
                        },
                    ]}
                    className=""
                />
            ),
        },
    ];

    return (
        <div className="flex flex-col h-full space-y-4">
            {/* Top Card / Container */}
            <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col min-h-0 flex-1">

                {/* Actions Bar */}
                <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6 border border-white rounded-lg">
                    <FilterTabs
                        tabs={TABS}
                        activeTab={activeTab}
                        onTabChange={setActiveTab}
                    />

                    <div className="flex items-center gap-6 w-full sm:w-auto">
                        <SearchInput
                            placeholder="Search Customer"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button
                            onClick={handleAddCustomer}
                            className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg font-normal whitespace-nowrap px-4 py-3 flex items-center gap-2"
                        >
                            <Plus size={14} />
                            Add Customer
                        </Button>
                    </div>
                </div>

                {/* Table */}
                <div className="flex-1 overflow-auto min-h-0 rounded-b-lg border border-white">
                    {isLoading ? (
                        <div className="flex items-center justify-center h-40 text-gray-400">
                            Loading invoices...
                        </div>
                    ) : invoices.length === 0 ? (
                        <div className="flex items-center justify-center h-40 text-gray-400">
                            No invoices found.
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
