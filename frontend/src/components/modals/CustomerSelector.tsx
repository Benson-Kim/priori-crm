import { getCustomer, getCustomers, type CustomerSummary } from "@/services/customerApi";
import { ChevronDown, CircleUserRound, Plus, Search, SquarePen } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { AddCustomerModal } from "./AddCustomerModal";

interface CustomerDropdownProps {
    onClose: () => void;
    onAddNew: () => void;
    searchQuery: string;
    onSearchChange: (value: string) => void;
    customers: CustomerSummary[];
    onSelectCustomer: (customer: CustomerSummary) => void;
}

function CustomerDropdown({
    onClose,
    onAddNew,
    searchQuery,
    onSearchChange,
    customers,
    onSelectCustomer,
}: CustomerDropdownProps) {
    const [isFocused, setIsFocused] = useState(false);

    return (
        <div className="absolute z-20 top-full mt-2 w-143 p-6 bg-white border border-gray-200 rounded-lg shadow-xl max-h-96 overflow-y-auto left-0">
            <Button
                variant="outline"
                onClick={() => {
                    onClose();
                    onAddNew();
                }}
                className="w-full text-[20px] mb-6"
            >
                <Plus size={20} /> Add New Customer
            </Button>
            <div className="border-b border-gray-100 flex flex-col gap-2 mb-2">
                <span className="capitalize font-bold text-gray-800 leading-8">Search Existing Customer</span>
                <div className="[&>div>div]:focus-within:bg-white! transition-all duration-100">
                    <Input
                        type="text"
                        placeholder="Search"
                        value={searchQuery}
                        onChange={(e) => onSearchChange(e.target.value)}
                        onFocus={() => setIsFocused(true)}
                        onBlur={() => setIsFocused(false)}
                        suffix={isFocused ? <Search size={20} /> : <ChevronDown size={20} />}
                    />
                </div>
            </div>

            {customers.length === 0 ? (
                <p className="px-3 py-4 text-gray-400 text-center">No customers found</p>
            ) : (
                customers.map((c, index) => (
                    <button
                        key={c.id}
                        type="button"
                        className={`w-full text-left px-3 py-4 ${index === 0 ? 'font-bold' : 'font-normal'} text-gray-700 hover:bg-error-50 flex items-center gap-2`}
                        onClick={() => onSelectCustomer(c)}
                    >
                        <CircleUserRound size={24} stroke="#475467" />
                        <span className={`block text-[14px] text-gray-800 `}>
                            {c.display_name}
                            {c.phone ? ` (${c.phone})` : ""}
                        </span>
                    </button>
                ))
            )}
        </div>
    );
}

export interface CustomerSelectorProps {
    label?: string;
    initialCustomerId?: string;
    initialCustomerName?: string;
    initialCustomerDetails?: {
        address?: string;
        // Optional on the customer record, so absent for anyone recorded
        // without contact details.
        phone?: string;
        email?: string;
    } | null;
    onChange: (customerId: string, currency?: string) => void;
    restrictedMode?: boolean;
    error?: string;
}

export function CustomerSelector({
    label = "To",
    initialCustomerId,
    initialCustomerName,
    initialCustomerDetails,
    onChange,
    restrictedMode = false,
    error,
}: CustomerSelectorProps) {
    const [customerId, setCustomerId] = useState(initialCustomerId || "");
    const [customerName, setCustomerName] = useState(initialCustomerName || "");
    const [customerDetails, setCustomerDetails] = useState(initialCustomerDetails || null);

    const [showDropdown, setShowDropdown] = useState(false);
    const [showAddModal, setShowAddModal] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const [customers, setCustomers] = useState<CustomerSummary[]>([]);


    const fetchCustomers = useCallback(async () => {
        try {
            const data = await getCustomers({ search: searchQuery, status: "active", per_page: 20 });
            setCustomers(data.items);
        } catch (err) {
            console.error("[CustomerSelector] search failed:", err);
        }
    }, [searchQuery]);

    useEffect(() => {
        if (showDropdown) void (async () => { await fetchCustomers(); })();
    }, [showDropdown, fetchCustomers]);

    const selectCustomer = async (c: CustomerSummary) => {
        setCustomerId(c.id);
        setCustomerName(c.display_name);
        setShowDropdown(false);
        setSearchQuery("");
        onChange(c.id, c.currency ?? undefined);

        try {
            const customerData = await getCustomer(c.id);
            setCustomerDetails({
                address: customerData.customer.address || undefined,
                phone: customerData.customer.phone ?? undefined,
                email: customerData.customer.email ?? undefined,
            });
            onChange(c.id, customerData.customer.currency ?? undefined);
        } catch (err) {
            console.error("[CustomerSelector] Failed to fetch customer details:", err);
            setCustomerDetails({
                phone: c.phone ?? undefined,
                email: c.email ?? undefined,
            });
        }
    };

    const handleCustomerAdded = async (newCustomerId: string, newCustomerName: string) => {
        setCustomerId(newCustomerId);
        setCustomerName(newCustomerName);
        onChange(newCustomerId);

        try {
            const customerData = await getCustomer(newCustomerId);
            setCustomerDetails({
                address: customerData.customer.address || undefined,
                phone: customerData.customer.phone ?? undefined,
                email: customerData.customer.email ?? undefined,
            });
            onChange(newCustomerId, customerData.customer.currency ?? undefined);
        } catch (err) {
            console.error("[CustomerSelector] Failed to fetch new customer details:", err);
        }
    };

    const dropdownRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setShowDropdown(false);
            }
        }
        if (showDropdown) {
            document.addEventListener("mousedown", handleClickOutside);
        }
        return () => {
            document.removeEventListener("mousedown", handleClickOutside);
        };
    }, [showDropdown]);

    return (
            <div className="flex flex-col gap-1" ref={dropdownRef}>
            <p className="text-sm text-gray-500 mb-1">{label}</p>

            {restrictedMode ? (
                <div className="flex flex-col gap-1">
                    <p className="text-[16px] font-bold text-priori-purple">{customerName || customerId}</p>
                    {customerDetails && (
                        <>
                            {customerDetails.address && (
                                <p className="text-sm text-gray-600">{customerDetails.address}</p>
                            )}
                            {customerDetails.phone && (
                                <p className="text-sm text-gray-600">{customerDetails.phone}</p>
                            )}
                            {customerDetails.email && (
                                <p className="text-sm text-gray-600">{customerDetails.email}</p>
                            )}
                        </>
                    )}
                </div>
            ) : customerId && customerDetails ? (
                <div className="flex flex-col gap-1">
                    <div className="relative">
                        <button
                            type="button"
                            className="text-priori-purple font-bold text-[16px] flex items-center gap-1 hover:underline text-left w-fit"
                            onClick={() => setShowDropdown(!showDropdown)}
                        >
                            {customerName} <SquarePen size={18} />
                        </button>

                        {showDropdown && (
                            <CustomerDropdown
                                onClose={() => setShowDropdown(false)}
                                onAddNew={() => setShowAddModal(true)}
                                searchQuery={searchQuery}
                                onSearchChange={setSearchQuery}
                                customers={customers}
                                onSelectCustomer={selectCustomer}
                            />
                        )}
                    </div>
                    {customerDetails.address && (
                        <p className="text-sm text-gray-600">{customerDetails.address}</p>
                    )}
                    {customerDetails.phone && (
                        <p className="text-sm text-gray-600">{customerDetails.phone}</p>
                    )}
                    {customerDetails.email && (
                        <p className="text-sm text-gray-600">{customerDetails.email}</p>
                    )}
                </div>
            ) : (
                <div className="relative">
                    <button
                        type="button"
                                className="text-priori-purple font-bold text-[16px] flex items-center gap-1 hover:underline text-left cursor-pointer"
                        onClick={() => setShowDropdown(!showDropdown)}
                    >
                        Add / Select Customer <SquarePen size={18} />
                    </button>

                    {showDropdown && (
                        <CustomerDropdown
                            onClose={() => setShowDropdown(false)}
                            onAddNew={() => setShowAddModal(true)}
                            searchQuery={searchQuery}
                            onSearchChange={setSearchQuery}
                            customers={customers}
                            onSelectCustomer={selectCustomer}
                        />
                    )}
                    {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
                </div>
            )}

            <AddCustomerModal
                isOpen={showAddModal}
                onClose={() => setShowAddModal(false)}
                onCustomerAdded={handleCustomerAdded}
            />
        </div>
    );
}
