import { useDebounce } from "@/hooks/useDebounce";
import { getVendor, getVendors, type VendorSummary } from "@/services/vendorApi";
import { Building2, ChevronDown, Plus, Search, SquarePen } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { VendorModal } from "./VendorModal";

interface VendorDropdownProps {
    onClose: () => void;
    onAddNew: () => void;
    searchQuery: string;
    onSearchChange: (value: string) => void;
    vendors: VendorSummary[];
    onSelectVendor: (vendor: VendorSummary) => void;
}

function VendorDropdown({
    onClose,
    onAddNew,
    searchQuery,
    onSearchChange,
    vendors,
    onSelectVendor,
}: VendorDropdownProps) {
    const [isFocused, setIsFocused] = useState(false);

    return (
        <div className="absolute z-20 top-full mt-2 w-[572px] p-6 bg-white border border-gray-200 rounded-lg shadow-xl max-h-96 overflow-y-auto left-0">
            <Button
                variant="outline"
                onClick={() => {
                    onClose();
                    onAddNew();
                }}
                className="w-full text-[20px] mb-6"
            >
                <Plus size={20} /> Add New Vendor
            </Button>
            <div className="border-b border-gray-100 flex flex-col gap-2 mb-2">
                <span className="capitalize font-bold text-gray-800 leading-8">Search Existing Vendor</span>
                <div className="[&>div>div]:focus-within:!bg-white transition-all duration-100">
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

            {vendors.length === 0 ? (
                <p className="px-3 py-4 text-gray-400 text-center">No vendors found</p>
            ) : (
                vendors.map((v, index) => (
                    <button
                        key={v.id}
                        type="button"
                        className={`w-full text-left px-3 py-4 ${index === 0 ? 'font-bold' : 'font-normal'} text-gray-700 hover:bg-error-50 flex items-center gap-2`}
                        onClick={() => onSelectVendor(v)}
                    >
                        <Building2 size={24} stroke="#475467" />
                        <span className={`block text-[14px] text-gray-800 `}>
                            {v.vendor_name} {v.email ? `(${v.email})` : ''}
                        </span>
                    </button>
                ))
            )}
        </div>
    );
}

export interface VendorSelectorProps {
    label?: string;
    initialVendorId?: string;
    initialVendorName?: string;
    initialVendorDetails?: {
        address?: string;
        phone: string;
        email: string;
    } | null;
    onChange: (vendorId: string) => void;
    restrictedMode?: boolean;
    error?: string;
}

export function VendorSelector({
    label = "To",
    initialVendorId,
    initialVendorName,
    initialVendorDetails,
    onChange,
    restrictedMode = false,
    error,
}: VendorSelectorProps) {
    const [vendorId, setVendorId] = useState(initialVendorId || "");
    const [vendorName, setVendorName] = useState(initialVendorName || "");
    const [vendorDetails, setVendorDetails] = useState(initialVendorDetails || null);

    const [showDropdown, setShowDropdown] = useState(false);
    const [showAddModal, setShowAddModal] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const [vendors, setVendors] = useState<VendorSummary[]>([]);

    const debouncedSearch = useDebounce(searchQuery, 300);

    const fetchVendors = useCallback(async () => {
        try {
            const data = await getVendors({ search: debouncedSearch, status: "active", per_page: 20 });
            setVendors(data.items);
        } catch (err) {
            console.error("[VendorSelector] search failed:", err);
        }
    }, [debouncedSearch]);

    useEffect(() => {
        if (showDropdown) void (async () => { await fetchVendors(); })();
    }, [showDropdown, fetchVendors]);

    const selectVendor = async (v: VendorSummary) => {
        setVendorId(v.id);
        setVendorName(v.vendor_name);
        setShowDropdown(false);
        setSearchQuery("");
        onChange(v.id);

        try {
            const vendorData = await getVendor(v.id);
            setVendorDetails({
                address: vendorData.address || undefined,
                phone: vendorData.phone_primary || "",
                email: vendorData.email || "",
            });
        } catch (err) {
            console.error("[VendorSelector] Failed to fetch vendor details:", err);
            setVendorDetails({
                phone: v.phone_primary || "",
                email: v.email || "",
            });
        }
    };

    const handleVendorAdded = async (newVendorId?: string, newVendorName?: string) => {
        setShowAddModal(false);
        if (newVendorId && newVendorName) {
            setVendorId(newVendorId);
            setVendorName(newVendorName);
            onChange(newVendorId);

            try {
                const vendorData = await getVendor(newVendorId);
                setVendorDetails({
                    address: vendorData.address || undefined,
                    phone: vendorData.phone_primary || "",
                    email: vendorData.email || "",
                });
            } catch (err) {
                console.error("[VendorSelector] Failed to fetch new vendor details:", err);
            }
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
                    <p className="text-[16px] font-bold text-priori-purple">{vendorName || vendorId}</p>
                    {vendorDetails && (
                        <>
                            {vendorDetails.address && (
                                <p className="text-sm text-gray-600">{vendorDetails.address}</p>
                            )}
                            <p className="text-sm text-gray-600">{vendorDetails.phone}</p>
                            <p className="text-sm text-gray-600">{vendorDetails.email}</p>
                        </>
                    )}
                </div>
            ) : vendorId && vendorDetails ? (
                <div className="flex flex-col gap-1">
                    <div className="relative">
                        {/* <button
                            type="button"
                            className="text-priori-purple font-bold text-[16px] flex items-center gap-1 hover:underline text-left w-fit"
                            onClick={() => setShowDropdown(!showDropdown)}
                        >
                            {vendorName} <SquarePen size={18} />
                        </button> */}

                            <div
                                onClick={() => setShowDropdown(!showDropdown)}
                                className="flex items-center justify-between w-full max-w-[320px] py-3 px-3 gap-3 rounded-lg border border-gray-300 cursor-pointer hover:border-priori-purple transition-all"
                            >
                                <span className="text-[16px] font-medium text-gray-900 truncate">
                                    {vendorName || "Select vendor"}
                                </span>

                                <ChevronDown size={16} className="text-gray-500" />
                            </div>

                            {showDropdown && (
                                <VendorDropdown
                                    onClose={() => setShowDropdown(false)}
                                    onAddNew={() => setShowAddModal(true)}
                                    searchQuery={searchQuery}
                                    onSearchChange={setSearchQuery}
                                    vendors={vendors}
                                    onSelectVendor={selectVendor}
                                />
                            )}
                        </div>
                        {vendorDetails.address && (
                            <p className="text-sm text-gray-600">{vendorDetails.address}</p>
                        )}
                        <p className="text-sm text-gray-600">{vendorDetails.phone}</p>
                        <p className="text-sm text-gray-600">{vendorDetails.email}</p>
                    </div>
                ) : (
                    <div className="relative">
                        <button
                            type="button"
                            className="text-priori-purple font-bold text-[16px] flex items-center gap-1 hover:underline text-left"
                            onClick={() => setShowDropdown(!showDropdown)}
                        >
                            Select Vendor <SquarePen size={18} />
                        </button>

                            {showDropdown && (
                                <VendorDropdown
                                    onClose={() => setShowDropdown(false)}
                                    onAddNew={() => setShowAddModal(true)}
                                    searchQuery={searchQuery}
                                    onSearchChange={setSearchQuery}
                                    vendors={vendors}
                                    onSelectVendor={selectVendor}
                                />
                            )}
                            {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
                        </div>
            )}

            <VendorModal
                isOpen={showAddModal}
                onClose={() => setShowAddModal(false)}
                onSuccess={handleVendorAdded}
            />
        </div>
    );
}