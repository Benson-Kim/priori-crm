import {
    getInvoiceCounts,
    getInvoices,
    markAsSent,
    type InvoiceStatusCounts,
    type InvoiceSummary,
} from "@/lib/invoiceApi";
import {
    CheckCircle,
    ChevronDown,
    ChevronLeft,
    ChevronRight,
    Download,
    Eye,
    Plus,
    Search,
} from "lucide-react";
import { useCallback, useEffect, useState, useRef } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";

// Click Outside Hook
function useOnClickOutside(ref: React.RefObject<HTMLElement | null>, handler: () => void) {
    useEffect(() => {
        const listener = (event: MouseEvent | TouchEvent) => {
            if (!ref.current || ref.current.contains(event.target as Node)) {
                return;
            }
            handler();
        };
        document.addEventListener("mousedown", listener);
        document.addEventListener("touchstart", listener);
        return () => {
            document.removeEventListener("mousedown", listener);
            document.removeEventListener("touchstart", listener);
        };
    }, [ref, handler]);
}

// Custom Action Dropdown perfectly matching Figma
function ActionDropdown({ invoice, onApprove }: { invoice: InvoiceSummary, onApprove: (i: InvoiceSummary) => void }) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState({ top: 0, left: 0 });
    const navigate = useNavigate();
    const containerRef = useRef<HTMLDivElement>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);
    
    useOnClickOutside(containerRef, (e) => {
        // Also check if click is outside the portal
        if (dropdownRef.current && dropdownRef.current.contains(e.target as Node)) {
            return;
        }
        setOpen(false);
    });

    const toggle = (e: React.MouseEvent) => {
        e.stopPropagation();
        if (!open && containerRef.current) {
            const rect = containerRef.current.getBoundingClientRect();
            // Position right-aligned to the button
            setCoords({ top: rect.bottom + 8, left: rect.right - 128 }); // 128px is w-32
        }
        setOpen(!open);
    };

    return (
        <div className="relative" ref={containerRef}>
            <button 
                onClick={toggle} 
                className="text-priori-purple flex items-center gap-1 hover:underline text-[14px]"
            >
                Actions <ChevronDown size={14} />
            </button>
            {open && createPortal(
                <div 
                    ref={dropdownRef}
                    style={{ top: coords.top, left: coords.left }}
                    className="fixed w-32 bg-white border border-gray-200 rounded-lg shadow-xl z-[9999] overflow-hidden flex flex-col"
                >
                    <button 
                        onClick={() => navigate(`/invoices/${invoice.id}`)} 
                        className="flex items-center justify-between px-3 py-2.5 bg-priori-purple text-white hover:bg-priori-purple/90 text-sm font-medium transition-colors"
                    >
                        <div className="flex items-center gap-2"><Eye size={16} /> View</div>
                        <ChevronRight size={14} />
                    </button>
                    <button 
                        onClick={() => { onApprove(invoice); setOpen(false); }} 
                        className="flex items-center gap-2 px-3 py-2.5 bg-white text-gray-700 hover:bg-gray-50 text-sm transition-colors"
                    >
                        <CheckCircle size={16} className="text-gray-400" /> Approve
                    </button>
                </div>,
                document.body
            )}
        </div>
    )
}

export default function InvoicesPage() {
    const [activeTab, setActiveTab] = useState("all");
    const [search, setSearch] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const [perPage, setPerPage] = useState(10);
    const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
    const [totalPages, setTotalPages] = useState(1);
    const [counts, setCounts] = useState<InvoiceStatusCounts>({
        all: 0, draft: 0, sent: 0, partial: 0, paid: 0, overdue: 0, canceled: 0,
    });
    const [isLoading, setIsLoading] = useState(true);
    const navigate = useNavigate();

    const fetchInvoices = useCallback(async () => {
        setIsLoading(true);
        try {
            const statusMap: Record<string, string | undefined> = {
                all: undefined,
                pending: "draft",
                paid: "paid",
                overdue: "overdue",
            };
            const data = await getInvoices({
                page: currentPage,
                per_page: perPage,
                status: statusMap[activeTab],
                search: search || undefined,
            });
            setInvoices(data.items);
            setTotalPages(data.total_pages);
        } catch (err) {
            console.error("[InvoicesPage] Failed to fetch invoices:", err);
        } finally {
            setIsLoading(false);
        }
    }, [currentPage, perPage, activeTab, search]);

    const fetchCounts = useCallback(async () => {
        try {
            const data = await getInvoiceCounts();
            setCounts(data);
        } catch (err) {
            console.error("[InvoicesPage] Failed to fetch counts:", err);
        }
    }, []);

    useEffect(() => { fetchInvoices(); }, [fetchInvoices]);
    useEffect(() => { fetchCounts(); }, [fetchCounts]);

    useEffect(() => { setCurrentPage(1); }, [activeTab, search]);

    const handleApprove = async (invoice: InvoiceSummary) => {
        try {
            // Using markAsSent to act as "Approve" functionality
            await markAsSent(invoice.id);
            fetchInvoices();
            fetchCounts();
        } catch (err) {
            console.error("[InvoicesPage] Approve failed:", err);
        }
    };

    const TABS = [
        { key: "all", label: "All", count: counts.all },
        { key: "pending", label: "Pending", count: counts.draft },
        { key: "paid", label: "Paid", count: counts.paid },
        { key: "overdue", label: "Overdue", count: counts.overdue },
    ];

    const getStatusElement = (item: InvoiceSummary) => {
        const status = item.status.toLowerCase();
        if (item.is_overdue && item.days_overdue > 0) {
            return <span className="text-danger font-medium">Overdue ({item.days_overdue} days)</span>;
        }
        if (status === "paid") return <span className="text-status-paid font-medium">Paid</span>;
        if (status === "sent") return <span className="text-status-sent font-medium">Sent</span>;
        return <span className="text-gray-500 font-medium">Draft</span>;
    };

    const renderAmount = (amount: number, currency: string) => {
        const sym = currency === "KES" ? "Ksh" : currency;
        return `${sym} ${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    };

    // Format Date specific to Figma (e.g. "07 Mar 2026")
    const formatDisplayDate = (dString: string) => {
        if (!dString) return "";
        const d = new Date(dString);
        if (isNaN(d.getTime())) return dString;
        const day = d.getDate().toString().padStart(2, '0');
        const month = d.toLocaleString('en-US', { month: 'short' });
        const year = d.getFullYear();
        return `${day} ${month} ${year}`;
    };

    return (
        <div className="flex flex-col h-full space-y-6 font-sans">
            
            {/* Top Action Bar (Export Excel) */}
            <div className="flex justify-end mt-4">
                <button className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg bg-white text-[14px] text-gray-700 hover:bg-gray-50 transition-colors shadow-sm">
                    <Download size={16} /> Export Excel
                </button>
            </div>

            {/* Main Table Card */}
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm flex flex-col min-h-0 flex-1">
                
                {/* Header / Tabs / Search / Add */}
                <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center p-5 gap-4 border-b border-gray-100">
                    <div className="flex items-center gap-2">
                        {TABS.map(tab => (
                            <button 
                                key={tab.key}
                                onClick={() => setActiveTab(tab.key)}
                                className={`px-4 py-2 font-medium text-[15px] flex items-center gap-1 rounded-lg transition-colors ${
                                    activeTab === tab.key 
                                    ? "bg-purple-50 text-priori-purple" 
                                    : "text-gray-500 hover:text-gray-800"
                                }`}
                            >
                                {tab.label} ({tab.count})
                            </button>
                        ))}
                    </div>

                    <div className="flex items-center gap-4 w-full xl:w-auto">
                        <div className="relative w-full sm:w-64">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                            <input 
                                type="text" 
                                placeholder="Search invoices"
                                value={search}
                                onChange={e => setSearch(e.target.value)}
                                className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm outline-none focus:border-priori-purple placeholder-gray-400"
                            />
                        </div>
                        <button
                            onClick={() => navigate("/invoices/add")}
                            className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg font-medium whitespace-nowrap px-4 py-2 flex items-center gap-2 text-[14px] transition-colors"
                        >
                            <Plus size={16} /> Add Invoice
                        </button>
                    </div>
                </div>

                {/* Table Area */}
                <div className="flex-1 overflow-x-auto min-h-0 px-5 pt-2">
                    <table className="w-full text-[14px] text-left">
                        <thead>
                            <tr className="bg-gray-50 text-gray-800 rounded-t-lg">
                                <th className="px-4 py-4 font-bold rounded-tl-lg">#</th>
                                <th className="px-4 py-4 font-bold">Customer Name</th>
                                <th className="px-4 py-4 font-bold">Invoice No.</th>
                                <th className="px-4 py-4 font-bold">Date</th>
                                <th className="px-4 py-4 font-bold">Amount</th>
                                <th className="px-4 py-4 font-bold">Status</th>
                                <th className="px-4 py-4 font-bold rounded-tr-lg">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {isLoading ? (
                                <tr><td colSpan={7} className="text-center py-8 text-gray-400">Loading invoices...</td></tr>
                            ) : invoices.length === 0 ? (
                                <tr><td colSpan={7} className="text-center py-8 text-gray-400">No invoices found.</td></tr>
                            ) : (
                                invoices.map((item, index) => (
                                    <tr key={item.id} className="border-b border-gray-100 hover:bg-gray-50/50 transition-colors">
                                        <td className="px-4 py-4 text-gray-600">
                                            {(currentPage - 1) * perPage + index + 1}.
                                        </td>
                                        <td className="px-4 py-4 font-medium text-gray-800 max-w-[200px] truncate" title={item.customer_name}>
                                            {item.customer_name}
                                        </td>
                                        <td className="px-4 py-4 text-gray-500 max-w-[150px] truncate" title={item.invoice_number}>
                                            {item.invoice_number}
                                        </td>
                                        <td className="px-4 py-4 text-gray-800 font-medium">
                                            {formatDisplayDate(item.transaction_date)}
                                        </td>
                                        <td className="px-4 py-4 text-gray-600">
                                            {renderAmount(item.total_due, item.currency)}
                                        </td>
                                        <td className="px-4 py-4">
                                            {getStatusElement(item)}
                                        </td>
                                        <td className="px-4 py-4">
                                            <ActionDropdown invoice={item} onApprove={handleApprove} />
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>

                {/* Pagination Footer */}
                <div className="p-5 border-t border-gray-100 flex flex-col sm:flex-row justify-between items-center gap-4 text-sm text-gray-600 bg-white rounded-b-xl">
                    <div className="flex items-center gap-2">
                        <span>Show</span>
                        <div className="relative">
                            <select 
                                value={perPage} 
                                onChange={e => { setPerPage(Number(e.target.value)); setCurrentPage(1); }}
                                className="appearance-none border border-purple-100 bg-purple-50 text-priori-purple rounded px-3 py-1.5 outline-none font-medium text-[13px] pr-8 cursor-pointer"
                            >
                                <option value={10}>10</option>
                                <option value={20}>20</option>
                                <option value={50}>50</option>
                            </select>
                            <ChevronDown size={14} className="absolute right-2 top-1/2 -translate-y-1/2 text-priori-purple pointer-events-none" />
                        </div>
                        <span>per page</span>
                    </div>

                    <div className="flex items-center gap-1">
                        <button 
                            disabled={currentPage === 1}
                            onClick={() => setCurrentPage(c => c - 1)}
                            className="p-1.5 text-gray-400 hover:text-priori-purple disabled:opacity-30 disabled:hover:text-gray-400 transition-colors"
                        >
                            <ChevronLeft size={16} />
                        </button>
                        
                        <div className="flex items-center gap-1 mx-2">
                            {Array.from({ length: totalPages }).map((_, i) => {
                                const p = i + 1;
                                const isCurrent = currentPage === p;
                                // Simple logic to show a few pages around current, plus first and last
                                const show = p === 1 || p === totalPages || (p >= currentPage - 1 && p <= currentPage + 1);
                                
                                if (show) {
                                    return (
                                        <button
                                            key={p}
                                            onClick={() => setCurrentPage(p)}
                                            className={`w-8 h-8 rounded text-[13px] font-medium flex items-center justify-center transition-colors ${
                                                isCurrent 
                                                ? "border border-priori-purple text-priori-purple bg-white" 
                                                : "text-gray-600 hover:bg-gray-100"
                                            }`}
                                        >
                                            {p}
                                        </button>
                                    );
                                }
                                
                                // Show ellipses
                                if (p === currentPage - 2 || p === currentPage + 2) {
                                    return <span key={p} className="w-8 text-center text-gray-400">...</span>;
                                }
                                
                                return null;
                            })}
                        </div>

                        <button 
                            disabled={currentPage === totalPages || totalPages === 0}
                            onClick={() => setCurrentPage(c => c + 1)}
                            className="p-1.5 text-gray-400 hover:text-priori-purple disabled:opacity-30 disabled:hover:text-gray-400 transition-colors"
                        >
                            <ChevronRight size={16} />
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
