import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import { formatCurrency } from "@/lib/utils";
import { Ban, CheckCircle, Eye, Plus, Trash } from "lucide-react";
import { useState } from "react";

interface Customer {
    id: string;
    number: number;
    name: string;
    email: string;
    phone: string;
    balance: number;
    status: "Active" | "Inactive";
}

const CUSTOMERS_DATA: Customer[] = [
    { id: "1", number: 1, name: "VDAB Plug KE", email: "vdabpliugke.com", phone: "0700 000 000", balance: 0, status: "Active" },
    { id: "2", number: 2, name: "Visionary Tech", email: "visionarytech@email.com", phone: "0900 222 222", balance: 5000, status: "Active" },
    { id: "3", number: 3, name: "Visionary Tech", email: "bontis.com", phone: "0700 000 000", balance: 0, status: "Inactive" },
    { id: "4", number: 4, name: "Majesty Collect", email: "majestyco@email.com", phone: "0700 000 000", balance: 0, status: "Active" },
    { id: "5", number: 5, name: "Bon Tonies", email: "bontis.com", phone: "0700 000 000", balance: 0, status: "Inactive" },
    { id: "6", number: 6, name: "Frank Mueke", email: "frankmueke@email.com", phone: "0700 000 000", balance: 0, status: "Inactive" },
    { id: "7", number: 7, name: "Harmony Designs", email: "harmony@email.com", phone: "0800 111 111", balance: 0, status: "Active" },
    { id: "8", number: 8, name: "Aurora Innovations", email: "aurora@email.com", phone: "0700 333 333", balance: 0, status: "Active" },
    { id: "9", number: 9, name: "Aurora Innovations", email: "aurora@email.com", phone: "0700 333 333", balance: 0, status: "Active" },
    { id: "10", number: 10, name: "Aurora Innovations", email: "aurora@email.com", phone: "0700 333 333", balance: 0, status: "Active" },
];

const TABS = [
    { key: "all", label: "All", count: 160 },
    { key: "active", label: "Active", count: 100 },
    { key: "inactive", label: "Inactive", count: 60 },
];

export default function QuotesPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);

    const handleAction = (action: string, customer: Customer) => {
        console.log(`${action} customer:`, customer.name);
    };

    const columns = [
        {
            key: "number",
            header: "#.",
            render: (item: Customer) => <span className="text-content-primary ">{item.number}.</span>,
            className: "w-[50px]",
        },
        {
            key: "name",
            header: "Customer Name",
            render: (item: Customer) => <span className=" text-content-primary">{item.name}</span>,
        },
        {
            key: "email",
            header: "Email",
            render: (item: Customer) => <span className="text-gray-500">{item.email}</span>,
        },
        {
            key: "phone",
            header: "Phone",
            render: (item: Customer) => <span className="text-content-primary">{item.phone}</span>,
        },
        {
            key: "balance",
            header: "Balance",
            render: (item: Customer) => <span className="text-gray-500">{formatCurrency(item.balance, 'Ksh')}</span>,
        },
        {
            key: "status",
            header: "Status",
            render: (item: Customer) => (
                <Badge variant={item.status.toLowerCase() as "active" | "inactive"}>
                    {item.status}
                </Badge>
            ),
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: Customer) => (
                <Dropdown
                    items={[
                        {
                            key: "view",
                            label: "View",
                            icon: <Eye size={16} />,
                            onClick: () => handleAction("View", item),
                        },
                        {
                            key: "activate",
                            label: "Activate",
                            icon: <CheckCircle size={16} />,
                            onClick: () => handleAction("Activate", item),
                        },
                        {
                            key: "deactivate",
                            label: "Deactivate",
                            icon: <Ban size={16} />,
                            onClick: () => handleAction("Deactivate", item),
                        },
                        {
                            key: "delete",
                            label: "Delete",
                            icon: <Trash size={16} />,
                            danger: true,
                            onClick: () => handleAction("Delete", item),
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
                        <Button className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg font-normal whitespace-nowrap px-4 py-3 flex items-center gap-2">
                            <Plus size={14} />
                            Add Customer
                        </Button>
                    </div>
                </div>

                {/* Table */}
                <div className="flex-1 overflow-auto min-h-0 rounded-b-lg border border-white">
                    <Table
                        columns={columns}
                        data={CUSTOMERS_DATA}
                        rowKey={(item) => item.id}
                    />
                </div>
            </div>

            {/* Pagination */}
            <div className="py-3 px-4 border border-gray-200 mt-auto bg-white rounded-2xl ">
                <Pagination
                    currentPage={currentPage}
                    totalPages={9}
                    perPage={perPage}
                    onPageChange={setCurrentPage}
                    onPerPageChange={setPerPage}
                />
            </div>

        </div>
    );
}
