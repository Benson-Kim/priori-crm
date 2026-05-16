import { getCustomers, type CustomerSummary } from "@/lib/customerApi";
import type { InvoiceCreatePayload, InvoiceResponse, LineItemPayload } from "@/lib/invoiceApi";
import { formatCurrency } from "@/lib/utils";
import { Image as ImageIcon, Plus, Save, SquarePen, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

interface InvoiceFormProps {
    initialData?: InvoiceResponse;
    onSave: (data: InvoiceCreatePayload) => Promise<void>;
    isLoading: boolean;
    onCancel: () => void;
    restrictedMode?: boolean;
}

const CURRENCY_OPTIONS = ["KES", "USD", "EUR", "GBP"];

interface LineItemRow {
    key: string;
    description: string;
    quantity: string;
    unitPrice: string;
    taxType: string;
}

function createEmptyRow(): LineItemRow {
    return {
        key: crypto.randomUUID(),
        description: "",
        quantity: "1",
        unitPrice: "0",
        taxType: "no_tax",
    };
}

function calcLineTotal(qty: string, price: string): number {
    const q = parseFloat(qty) || 0;
    const p = parseFloat(price) || 0;
    return q * p;
}

export function InvoiceForm({
    initialData,
    onSave,
    isLoading,
    //  onCancel, 
    restrictedMode = false
}: Readonly<InvoiceFormProps>) {
    const [customerId, setCustomerId] = useState(initialData?.customer_id || "");
    const [customerSearch, setCustomerSearch] = useState("");
    const [customers, setCustomers] = useState<CustomerSummary[]>([]);
    const [showCustomerDropdown, setShowCustomerDropdown] = useState(false);
    const [selectedCustomerName, setSelectedCustomerName] = useState(initialData?.customer?.display_name || "");

    const today = new Date().toISOString().split("T")[0];
    const defaultDueDate = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];
    const [transactionDate, setTransactionDate] = useState(initialData?.transaction_date || today);
    const [dueDate, setDueDate] = useState(initialData?.due_date || defaultDueDate);
    const [currency, setCurrency] = useState(initialData?.currency || "KES");
    const [rfqNumber, setRfqNumber] = useState(initialData?.rfq_number || "");
    const [notes, setNotes] = useState(initialData?.notes || "");

    const [lineItems, setLineItems] = useState<LineItemRow[]>(() => {
        if (initialData?.line_items?.length) {
            return initialData.line_items.map((li) => ({
                key: li.id,
                description: li.description,
                quantity: String(li.quantity),
                unitPrice: String(li.unit_price),
                taxType: li.tax_type || "no_tax",
            }));
        }
        return [createEmptyRow()];
    });

    const [errors, setErrors] = useState<Record<string, string>>({});

    const fetchCustomers = useCallback(async () => {
        try {
            const data = await getCustomers({ search: customerSearch, status: "active", per_page: 20 });
            setCustomers(data.items);
        } catch (err) {
            console.error("[InvoiceForm] Customer search failed:", err);
        }
    }, [customerSearch]);

    useEffect(() => {
        if (showCustomerDropdown) fetchCustomers();
    }, [showCustomerDropdown, fetchCustomers]);

    const totals = useMemo(() => {
        let subtotal = 0;
        for (const item of lineItems) {
            subtotal += calcLineTotal(item.quantity, item.unitPrice);
        }
        // Strict to design: No tax, no discount shown in totals
        const totalDue = subtotal;
        return { subtotal, totalDue };
    }, [lineItems]);

    const addRow = () => setLineItems((prev) => [...prev, createEmptyRow()]);
    const removeRow = (key: string) => setLineItems((prev) => prev.filter((r) => r.key !== key));
    const updateRow = (key: string, field: keyof LineItemRow, value: string) => {
        setLineItems((prev) =>
            prev.map((r) => (r.key === key ? { ...r, [field]: value } : r))
        );
    };

    const selectCustomer = (c: CustomerSummary) => {
        setCustomerId(c.id);
        setSelectedCustomerName(c.display_name);
        setShowCustomerDropdown(false);
        setCustomerSearch("");
    };

    const handleSubmit = async () => {
        const newErrors: Record<string, string> = {};
        if (!customerId) newErrors.customer = "Customer is required";
        if (!transactionDate) newErrors.transactionDate = "Transaction date is required";
        if (!dueDate) newErrors.dueDate = "Due date is required";
        if (dueDate < transactionDate) newErrors.dueDate = "Due date must be on or after transaction date";

        const validItems = lineItems.filter((r) => r.description.trim());
        if (validItems.length === 0) newErrors.lineItems = "At least one line item is required";

        for (const item of validItems) {
            const qty = parseFloat(item.quantity);
            const price = parseFloat(item.unitPrice);
            if (isNaN(qty) || qty <= 0) newErrors[`item_${item.key}_qty`] = "Quantity must be > 0";
            if (isNaN(price) || price < 0) newErrors[`item_${item.key}_price`] = "Price must be >= 0";
        }

        setErrors(newErrors);
        if (Object.keys(newErrors).length > 0) return;

        const items: LineItemPayload[] = validItems.map((r) => ({
            description: r.description.trim(),
            quantity: parseFloat(r.quantity),
            unitPrice: parseFloat(r.unitPrice),
            taxType: r.taxType || "no_tax",
        }));

        const payload: InvoiceCreatePayload = {
            customerId,
            transactionDate,
            dueDate,
            currency,
            lineItems: items,
            rfqNumber: rfqNumber || undefined,
            notes: notes || undefined,
        };

        await onSave(payload);
    };

    // Helper to format date like "30th Mar 2026"
    const formatDisplayDate = (dString: string) => {
        if (!dString) return "";
        const d = new Date(dString);
        if (isNaN(d.getTime())) return dString;
        const day = d.getDate();
        const suffix = ["th", "st", "nd", "rd"][(day % 10 > 3 || Math.floor(day % 100 / 10) === 1) ? 0 : day % 10];
        const month = d.toLocaleString('en-US', { month: 'short' });
        const year = d.getFullYear();
        return `${day}${suffix} ${month} ${year}`;
    };

    // const [isEditingTxnDate, setIsEditingTxnDate] = useState(false);
    // const [isEditingDueDate, setIsEditingDueDate] = useState(false);

    return (
        <div className="flex flex-col gap-6 font-sans">
            <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden shadow-sm">



                {/* Top Section */}
                <div className="p-6">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        {/* Logo Column */}
                        <div className="flex flex-col items-start gap-3">
                            <div className="w-16 h-16 bg-gray-100 rounded-lg flex items-center justify-center border border-gray-200 text-gray-500">
                                <ImageIcon size={28} />
                            </div>
                            <button type="button" className="text-priori-purple font-bold text-sm flex items-center gap-1 hover:underline">
                                Update <SquarePen size={20} />
                            </button>
                        </div>

                        {/* Company Info */}
                        <div className="flex flex-col gap-1 text-gray-800">
                            <h3 className="font-bold text-base mb-1">Priori Technologies</h3>
                            <p className="text-sm">P.O Box 124,90600</p>
                            <p className="text-sm">+254712345678</p>
                            <p className="text-sm mb-1">priori@techmail.com</p>
                            <button type="button" className="text-priori-purple font-bold text-sm flex items-center gap-1 hover:underline w-fit">
                                Update <SquarePen size={20} />
                            </button>
                        </div>

                        {/* Invoice To */}
                        <div className="flex flex-col gap-1">
                            <h2 className="text-[22px] font-black text-gray-800 tracking-wider mb-1 uppercase">INVOICE</h2>
                            <p className="text-sm text-gray-500 mb-1">To</p>

                            {restrictedMode ? (
                                <p className="text-[16px] font-bold text-priori-purple">{selectedCustomerName || customerId}</p>
                            ) : (
                                <div className="relative">
                                    <button
                                        type="button"
                                        className="text-priori-purple font-bold text-[16px] flex items-center gap-1 hover:underline text-left"
                                        onClick={() => setShowCustomerDropdown(!showCustomerDropdown)}
                                    >
                                        {selectedCustomerName || "Add / Select Customer"} <SquarePen size={20} />
                                    </button>

                                    {showCustomerDropdown && (
                                        <div className="absolute z-20 top-full mt-2 left-0 w-64 bg-white border border-gray-200 rounded-lg shadow-xl max-h-60 overflow-y-auto">
                                            <div className="p-2 border-b border-gray-100">
                                                <input
                                                    type="text"
                                                    placeholder="Search..."
                                                    value={customerSearch}
                                                    onChange={(e) => setCustomerSearch(e.target.value)}
                                                    className="w-full text-sm outline-none px-2 py-1"
                                                />
                                            </div>
                                            {customers.length === 0 ? (
                                                <p className="p-3 text-sm text-gray-400">No customers found</p>
                                            ) : (
                                                customers.map((c) => (
                                                    <button
                                                        key={c.id}
                                                        type="button"
                                                        className="w-full text-left px-4 py-2 text-sm hover:bg-purple-50 text-gray-700"
                                                        onClick={() => selectCustomer(c)}
                                                    >
                                                        <span className="font-medium block">{c.display_name}</span>
                                                        <span className="text-gray-400 text-xs">{c.email}</span>
                                                    </button>
                                                ))
                                            )}
                                        </div>
                                    )}
                                    {errors.customer && <p className="text-xs text-red-500 mt-1">{errors.customer}</p>}
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                <div className="h-px bg-gray-300 w-full" />

                {/* Metadata Row */}
                <div className="p-8 grid grid-cols-2 md:grid-cols-5 gap-6">
                    <div className="flex flex-col gap-2">
                        <label className="text-[13px] text-gray-500 font-medium w-full">Reference</label>
                        <p className="text-[16px] font-bold text-gray-800">{initialData?.invoice_reference || "Autogenerated"}</p>
                    </div>

                    <div className="flex flex-col gap-2">
                        <label className="text-[13px] text-gray-500 font-medium w-full">Transaction Date</label>
                        <div className="flex items-center gap-2 relative w-full">
                            <span className="text-[16px] font-bold text-priori-purple pr-8 whitespace-nowrap">{formatDisplayDate(transactionDate)}</span>
                            {!restrictedMode && <SquarePen size={20} className="text-priori-purple absolute right-0 pointer-events-none" />}
                            {!restrictedMode && (
                                <input
                                    type="date"
                                    value={transactionDate}
                                    onChange={e => setTransactionDate(e.target.value)}
                                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer date-picker-overlay text-priori-purple"
                                />
                            )}
                        </div>
                        {errors.transactionDate && <p className="text-xs text-red-500">{errors.transactionDate}</p>}
                    </div>

                    <div className="flex flex-col gap-2">
                        <label className="text-[13px] text-gray-500 font-medium w-full">Due Date</label>
                        <div className="flex items-center gap-2 relative w-full">
                            <span className="text-[16px] font-bold text-priori-purple pr-8 whitespace-nowrap">{formatDisplayDate(dueDate)}</span>
                            {!restrictedMode && <SquarePen size={20} className="text-priori-purple absolute right-0 pointer-events-none" />}
                            {!restrictedMode && (
                                <input
                                    type="date"
                                    value={dueDate}
                                    onChange={e => setDueDate(e.target.value)}
                                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer date-picker-overlay text-priori-purple"
                                />
                            )}
                        </div>
                        {errors.dueDate && <p className="text-xs text-red-500">{errors.dueDate}</p>}
                    </div>

                    <div className="flex flex-col gap-2">
                        <label className="text-[13px] text-gray-500 font-medium w-full">RFQ/RFP Number</label>
                        <div className="flex items-center gap-2 w-full">
                            <input
                                type="text"
                                value={rfqNumber}
                                onChange={e => setRfqNumber(e.target.value)}
                                placeholder="Enter"
                                disabled={restrictedMode}
                                className="text-[16px] font-bold text-priori-purple outline-none bg-transparent w-full placeholder-priori-purple/70"
                            />
                            {!restrictedMode && <SquarePen size={20} className="text-priori-purple pointer-events-none shrink-0" />}
                        </div>
                    </div>

                    <div className="flex flex-col gap-2 relative group">
                        <label className="text-[13px] text-gray-500 font-medium w-full">Currency</label>
                        <div className="flex items-center gap-2 relative w-full">
                            <span className="text-[16px] font-bold text-priori-purple pr-8">{currency}</span>
                            {!restrictedMode && <SquarePen size={20} className="text-priori-purple absolute right-0 pointer-events-none" />}
                            {!restrictedMode && (
                                <select
                                    value={currency}
                                    onChange={e => setCurrency(e.target.value)}
                                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                >
                                    {CURRENCY_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
                                </select>
                            )}
                        </div>
                    </div>
                </div>

                <div className="h-px bg-gray-300 w-full" />

                {/* Line Items */}
                <div className="p-8 pb-4">
                    <h3 className="text-[17px] font-bold text-gray-800 mb-5">Item Details</h3>
                    {errors.lineItems && <p className="text-xs text-red-500 mb-2">{errors.lineItems}</p>}

                    <div className="mb-4">
                        <table className="w-full text-[16px]">
                            <thead>
                                <tr className="bg-priori-purple text-white">
                                    <th className="text-left px-4 py-4 font-semibold w-[20%]">Item</th>
                                    <th className="text-left px-4 py-4 font-semibold w-[40%]">Description</th>
                                    <th className="text-left px-4 py-4 font-semibold w-[15%]">Quantity</th>
                                    <th className="text-left px-4 py-4 font-semibold w-[15%]">Price</th>
                                    <th className="text-left px-4 py-4 font-semibold w-[10%]">Total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {lineItems.map((row) => {
                                    const lineTotal = calcLineTotal(row.quantity, row.unitPrice);
                                    return (
                                        <tr key={row.key} className="border-b border-gray-300 hover:bg-gray-100 transition-colors group relative">
                                            <td className="p-2 align-top">
                                                <input
                                                    type="text"
                                                    value={row.description.split('\n')[0] || ""}
                                                    onChange={(e) => updateRow(row.key, "description", e.target.value)}
                                                    placeholder="Item name"
                                                    disabled={restrictedMode}
                                                    className="w-full bg-transparent px-3 py-2 outline-none font-bold text-gray-500 placeholder-gray-500"
                                                    style={{ color: "transparent", textShadow: "0 0 0 var(--color-priori-purple)" }}
                                                />
                                            </td>
                                            <td className="p-2 align-top">
                                                <textarea
                                                    value={row.description}
                                                    onChange={(e) => updateRow(row.key, "description", e.target.value)}
                                                    placeholder="Detailed description"
                                                    disabled={restrictedMode}
                                                    rows={1}
                                                    className="w-full bg-transparent px-3 py-2 outline-none text-gray-800 resize-none min-h-6"
                                                />
                                            </td>
                                            <td className="p-2 align-top">
                                                <input
                                                    type="number"
                                                    value={row.quantity}
                                                    onChange={(e) => updateRow(row.key, "quantity", e.target.value)}
                                                    disabled={restrictedMode}
                                                    className="w-full bg-transparent px-3 py-2 outline-none text-left font-bold text-gray-800"
                                                />
                                            </td>
                                            <td className="p-2 align-top">
                                                <input
                                                    type="number"
                                                    value={row.unitPrice}
                                                    onChange={(e) => updateRow(row.key, "unitPrice", e.target.value)}
                                                    disabled={restrictedMode}
                                                    className="w-full bg-transparent px-3 py-2 outline-none text-left font-bold text-gray-800"
                                                />
                                            </td>
                                            <td className="px-4 py-4 text-left font-bold text-gray-800 align-top">
                                                {formatCurrency(lineTotal, "")}
                                            </td>

                                            {/* Delete button (shows on hover) */}
                                            {!restrictedMode && lineItems.length > 1 && (
                                                <div className="absolute right-0 top-3 translate-x-full opacity-0 group-hover:opacity-100 pl-2">
                                                    <button type="button" onClick={() => removeRow(row.key)} className="text-red-400 hover:text-red-600">
                                                        <Trash2 size={16} />
                                                    </button>
                                                </div>
                                            )}
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    {!restrictedMode && (
                        <button
                            type="button"
                            onClick={addRow}
                            className="flex items-center gap-2 px-5 py-2.5 border border-priori-purple text-priori-purple text-[16px] font-medium rounded-lg hover:bg-purple-50 transition-colors w-max"
                        >
                            <Plus size={16} /> Add an Item
                        </button>
                    )}
                </div>

                {/* Subtotals */}
                <div className="px-8 flex justify-end">
                    <div className="w-80">
                        <div className="flex justify-between items-center mb-6 text-gray-800">
                            <span className="text-[16px] font-bold ">Subtotal</span>
                            <span className="text-[16px]">{formatCurrency(totals.subtotal, "")}</span>
                        </div>
                        <div className="flex justify-between items-center mb-6 font-bold">
                            <span className="text-[16px] ">Total Due</span>
                            <span className="text-[16px] ">{formatCurrency(totals.totalDue, "")}</span>
                        </div>
                        <div className="h-px bg-gray-300 w-full mt-6" />
                    </div>
                </div>

                {/* Notes */}
                <div className="p-8 pt-6">
                    <label className="block text-[16px] font-bold text-gray-800 mb-3">Notes</label>
                    <textarea
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                        placeholder="Add notes here"
                        rows={1}
                        className="w-full p-4 border border-gray-300 rounded-xl text-[16px] outline-none focus:border-priori-purple resize-none placeholder-gray-400"
                    />
                </div>

            </div>

            {/* Footer Actions */}
            <div className="flex justify-end items-center gap-4 mt-2">
                <button
                    type="button"
                    onClick={() => alert("Preview not implemented")}
                    className="px-8 py-3 border border-priori-purple text-priori-purple font-medium text-[16px] rounded-lg hover:bg-purple-50 transition-colors bg-white"
                >
                    Preview
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={isLoading}
                    className="flex items-center gap-2 px-8 py-3 bg-priori-purple text-white font-medium text-[16px] rounded-lg hover:bg-priori-purple/90 transition-colors disabled:opacity-70"
                >
                    <Save size={18} /> Save &amp; Continue
                </button>
            </div>
        </div>
    );
}
