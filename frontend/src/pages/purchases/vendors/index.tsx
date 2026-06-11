import { VendorModal } from "@/components/modals/VendorModal";
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
    activateVendor,
    deactivateVendor,
    deleteVendor,
    getVendorCounts,
    getVendors,
    type PaginatedVendors,
    type VendorStatusCounts,
    type VendorSummary,
} from "@/services/vendorApi";
import { Ban, CheckCircle, Eye, Pencil, Plus, Trash } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function VendorsPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);
    const [vendors, setVendors] = useState<VendorSummary[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<VendorStatusCounts>({ all: 0, active: 0, inactive: 0 });
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const { showConfirm, ConfirmDialog } = useConfirm();
    const navigate = useNavigate();

    const fetchVendors = useCallback(async () => {
        setIsLoading(true);
        try {
            const data: PaginatedVendors = await getVendors({
                page: currentPage,
                per_page: perPage,
                status: activeTab !== "all" ? activeTab : undefined,
                search: search || undefined,
            });
            setVendors(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load vendors");
            console.error("Failed to fetch vendors:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getVendorCounts();
            setCounts(data);
        } catch (err) {
            console.error("Failed to fetch counts:", err);
        }
    }, []);

    const refreshAll = useCallback(() => {
        fetchVendors();
        fetchCounts();
    }, [fetchVendors, fetchCounts]);

    useEffect(() => {
        void (async () => { await fetchVendors(); })();
    }, [fetchVendors]);

    useEffect(() => {
        void (async () => { await fetchCounts(); })();
    }, [fetchCounts]);

    useEffect(() => {
        startTransition(() => { setCurrentPage(1); });
    }, [activeTab, search]);

    const handleView = (vendor: VendorSummary) => {
        navigate(`/vendors/${vendor.id}`);
    };

    const handleEdit = (vendor: VendorSummary) => {
        setEditVendorId(vendor.id);
        setIsModalOpen(true);
    };

    const handleActivate = async (vendor: VendorSummary) => {
        if (vendor.status === "active") return;
        try {
            await activateVendor(vendor.id);
            refreshAll();
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to activate vendor");
        }
    };

    const handleDeactivate = async (vendor: VendorSummary) => {
        if (vendor.status === "inactive") return;
        try {
            await deactivateVendor(vendor.id);
            refreshAll();
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to deactivate vendor");
        }
    };

    const handleDelete = (vendor: VendorSummary) => {
        showConfirm({
            title: "Delete Vendor?",
            description: "Are you sure you want to delete this vendor? This will permanently remove the vendor and cannot be undone.",
            confirmLabel: "Yes, delete vendor",
            variant: "danger",
            onConfirm: async () => {
                try {
                    await deleteVendor(vendor.id);
                    refreshAll();
                } catch (err) {
                    const msg = err instanceof Error ? err.message : String(err);
                    setError(msg);
                }
            }
        });
    };

    const getActions = (vendor: VendorSummary): DropdownItem[] => {
        const actions: DropdownItem[] = [
            {
                key: "view",
                label: "View",
                icon: <Eye size={16} />,
                onClick: () => handleView(vendor),
            },
            {
                key: "edit",
                label: "Edit",
                icon: <Pencil size={16} />,
                onClick: () => handleEdit(vendor),
            },
        ];

        if (vendor.status === "inactive") {
            actions.push({
                key: "activate",
                label: "Activate",
                icon: <CheckCircle size={16} />,
                onClick: () => handleActivate(vendor),
            });
        }

        if (vendor.status === "active") {
            actions.push({
                key: "deactivate",
                label: "Deactivate",
                icon: <Ban size={16} />,
                onClick: () => handleDeactivate(vendor),
            });
        }

        actions.push({
            key: "delete",
            label: "Delete",
            icon: <Trash size={16} />,
            danger: true,
            onClick: () => handleDelete(vendor),
        });

        return actions;
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
            render: (_item: VendorSummary, index: number) => <span className="text-content-primary ">{(currentPage - 1) * perPage + index + 1}.</span>,
            className: "w-[50px]",
        },
        {
            key: "name",
            header: "Vendor Name",
            render: (item: VendorSummary) => (
                <span className=" text-content-primary">
                    {item.vendor_name}
                </span>
            ),
        },
        {
            key: "email",
            header: "Email",
            render: (item: VendorSummary) => <span className="text-gray-500">{item.email}</span>,
        },
        {
            key: "phone",
            header: "Phone",
            render: (item: VendorSummary) => <span className="text-content-primary">{item.phone_primary}</span>,
        },
        {
            key: "payables",
            header: "Payables",
            render: (item: VendorSummary) => <span className="text-gray-500">{formatCurrency(Number(item.payables), item.currency ?? "Ksh")}</span>,
        },
        {
            key: "status",
            header: "Status",
            render: (item: VendorSummary) => (
                <Badge variant={item.status.toLowerCase() as "active" | "inactive"}>
                    {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
                </Badge>
            ),
        },
        {
            key: "actions",
            header: "Actions",
            className: "w-[120px]",
            render: (item: VendorSummary) => (
                <Dropdown
                    items={getActions(item)}
                />
            ),
        },
    ];

    // Modal State
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [editVendorId, setEditVendorId] = useState<string | null>(null);

    return (
        <div className="flex flex-col h-full space-y-4 font-sans">
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
                            placeholder="Search vendor"
                            value={search}
                            onSearchChange={setSearch}
                            className="w-full sm:w-70"
                        />
                        <Button
                            variant="primary"
                            onClick={() => {
                                setEditVendorId(null);
                                setIsModalOpen(true);
                            }}>
                            <Plus size={16} /> Add Vendor
                        </Button>
                    </div>
                </div>

                {/* Table */}
                <div className="overflow-x-auto rounded-b-lg border border-white">
                    {isLoading ? (
                        <LoadingState message="Loading vendors..." />
                    ) : (
                        <Table
                            columns={columns}
                            data={vendors}
                            rowKey={(item) => item.id}
                            emptyMessage="No vendors found."
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

            <VendorModal
                isOpen={isModalOpen}
                onClose={() => {
                    setIsModalOpen(false);
                    setEditVendorId(null);
                }}
                vendorId={editVendorId}
                onSuccess={() => {
                    refreshAll();
                    setIsModalOpen(false);
                    setEditVendorId(null);
                }}
            />
        </div>
    );
}
