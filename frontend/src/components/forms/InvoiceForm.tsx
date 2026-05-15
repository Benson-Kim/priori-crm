import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import type { InvoiceCreatePayload, InvoiceResponse, LineItemPayload } from "@/lib/invoiceApi";
import { getCustomers, type CustomerSummary } from "@/lib/customerApi";
import { formatCurrency } from "@/lib/utils";
import { Plus, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

interface InvoiceFormProps {
    initialData?: InvoiceResponse;
    onSave: (data: InvoiceCreatePayload) => Promise<void>;
    isLoading: boolean;
    onCancel: () => void;
    /** Restrict editing for Sent invoices */
    restrictedMode?: boolean;
}

const TAX_OPTIONS = [
    { value: "vat_16", label: "VAT (16%)" },
    { value: "vat_8", label: "VAT (8%)" },
    { value: "no_tax", label: "No Tax" },
];

const CURRENCY_OPTIONS = [
    { value: "KES", label: "KES" },
    { value: "USD", label: "USD" },
    { value: "EUR", label: "EUR" },
    { value: "GBP", label: "GBP" },
];

const DISCOUNT_TYPE_OPTIONS = [
    { value: "", label: "No Discount" },
    { value: "amount", label: "Fixed Amount" },
    { value: "percentage", label: "Percentage" },
];

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
        taxType: "vat_16",
    };
}

function calcLineTotal(qty: string, price: string): number {
    const q = parseFloat(qty) || 0;
    const p = parseFloat(price) || 0;
    return q * p;
}

function calcTaxAmount(lineTotal: number, taxType: string): number {
    if (taxType === "vat_16") return lineTotal * 0.16;
    if (taxType === "vat_8") return lineTotal * 0.08;
    return 0;
}

export function InvoiceForm({ initialData, onSave, isLoading, onCancel, restrictedMode = false }: InvoiceFormProps) {
    // Customer selection
    const [customerId, setCustomerId] = useState(initialData?.customer_id || "");
    const [customerSearch, setCustomerSearch] = useState("");
    const [customers, setCustomers] = useState<CustomerSummary[]>([]);
    const [showCustomerDropdown, setShowCustomerDropdown] = useState(false);
    const [selectedCustomerName, setSelectedCustomerName] = useState("");

    // Metadata
    const today = new Date().toISOString().split("T")[0];
    const defaultDueDate = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];
    const [transactionDate, setTransactionDate] = useState(initialData?.transaction_date || today);
    const [dueDate, setDueDate] = useState(initialData?.due_date || defaultDueDate);
    const [currency, setCurrency] = useState(initialData?.currency || "KES");
    const [rfqNumber, setRfqNumber] = useState(initialData?.rfq_number || "");
    const [notes, setNotes] = useState(initialData?.notes || "");

    // Line items
    const [lineItems, setLineItems] = useState<LineItemRow[]>(() => {
        if (initialData?.line_items?.length) {
            return initialData.line_items.map((li) => ({
                key: li.id,
                description: li.description,
                quantity: String(li.quantity),
                unitPrice: String(li.unit_price),
                taxType: li.tax_type,
            }));
        }
        return [createEmptyRow()];
    });

    // Discount
    const [discountType, setDiscountType] = useState(initialData?.discount_type || "");
    const [discountAmount, setDiscountAmount] = useState(String(initialData?.discount_amount || ""));
    const [discountPercentage, setDiscountPercentage] = useState(String(initialData?.discount_percentage || ""));

    // Errors
    const [errors, setErrors] = useState<Record<string, string>>({});

    // Fetch customers for dropdown
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

    // Calculate totals
    const totals = useMemo(() => {
        let subtotal = 0;
        let taxTotal = 0;
        for (const item of lineItems) {
            const lineTotal = calcLineTotal(item.quantity, item.unitPrice);
            subtotal += lineTotal;
            taxTotal += calcTaxAmount(lineTotal, item.taxType);
        }

        let discountValue = 0;
        if (discountType === "amount") {
            discountValue = parseFloat(discountAmount) || 0;
        } else if (discountType === "percentage") {
            discountValue = subtotal * ((parseFloat(discountPercentage) || 0) / 100);
        }

        const totalDue = subtotal - discountValue + taxTotal;
        return { subtotal, taxTotal, discountValue, totalDue };
    }, [lineItems, discountType, discountAmount, discountPercentage]);

    // Line item handlers
    const addRow = () => setLineItems((prev) => [...prev, createEmptyRow()]);
    const removeRow = (key: string) => setLineItems((prev) => prev.filter((r) => r.key !== key));
    const updateRow = (key: string, field: keyof LineItemRow, value: string) => {
        setLineItems((prev) =>
            prev.map((r) => (r.key === key ? { ...r, [field]: value } : r))
        );
    };

    // Select customer
    const selectCustomer = (c: CustomerSummary) => {
        setCustomerId(c.id);
        setSelectedCustomerName(c.display_name);
        setShowCustomerDropdown(false);
        setCustomerSearch("");
    };

    // Validation + Submit
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
        if (Object.keys(newErrors).length > 0) {
            console.log("[InvoiceForm] Validation errors:", newErrors);
            return;
        }

        const items: LineItemPayload[] = validItems.map((r) => ({
            description: r.description.trim(),
            quantity: parseFloat(r.quantity),
            unitPrice: parseFloat(r.unitPrice),
            taxType: r.taxType,
        }));

        const payload: InvoiceCreatePayload = {
            customerId,
            transactionDate,
            dueDate,
            currency,
            lineItems: items,
            rfqNumber: rfqNumber || undefined,
            notes: notes || undefined,
            discountType: discountType || undefined,
            discountAmount: discountType === "amount" ? parseFloat(discountAmount) || undefined : undefined,
            discountPercentage: discountType === "percentage" ? parseFloat(discountPercentage) || undefined : undefined,
        };

        console.log("[InvoiceForm] Submitting:", payload);
        await onSave(payload);
    };

    return (
        <div className="bg-white rounded-2xl border border-gray-200 p-8">
            {/* Sender + Recipient */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mb-8">
                {/* Sender */}
                <div>
                    <h3 className="text-lg font-bold text-gray-800 mb-2">Priori Technologies</h3>
                    <p className="text-sm text-gray-500">P.O Box 124, 90600</p>
                    <p className="text-sm text-gray-500">+254712345678</p>
                    <p className="text-sm text-gray-500">priori@techmail.com</p>
                </div>

                {/* Recipient / Customer Selector */}
                <div>
                    <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Invoice</p>
                    <p className="text-sm font-medium text-gray-600 mb-2">To</p>
                    {restrictedMode ? (
                        <p className="text-sm font-semibold text-gray-800">{selectedCustomerName || customerId}</p>
                    ) : (
                        <div className="relative">
                            <div
                                className="flex items-center gap-2 p-3 border border-gray-300 rounded-lg cursor-pointer hover:border-priori-purple"
                                onClick={() => setShowCustomerDropdown(!showCustomerDropdown)}
                            >
                                <Search size={16} className="text-gray-400" />
                                <span className={selectedCustomerName ? "text-gray-800 font-medium" : "text-gray-400"}>
                                    {selectedCustomerName || "Add / Select Customer"}
                                </span>
                            </div>
                            {showCustomerDropdown && (
                                <div className="absolute z-20 top-full mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-60 overflow-y-auto">
                                    <div className="p-2">
                                        <Input
                                            type="text"
                                            placeholder="Search customers..."
                                            value={customerSearch}
                                            onChange={(e) => setCustomerSearch(e.target.value)}
                                            className="text-sm"
                                        />
                                    </div>
                                    {customers.length === 0 ? (
                                        <p className="p-3 text-sm text-gray-400">No customers found</p>
                                    ) : (
                                        customers.map((c) => (
                                            <button
                                                key={c.id}
                                                type="button"
                                                className="w-full text-left px-4 py-2 text-sm hover:bg-gray-50 text-gray-700"
                                                onClick={() => selectCustomer(c)}
                                            >
                                                <span className="font-medium">{c.display_name}</span>
                                                <span className="text-gray-400 ml-2">{c.email}</span>
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

            {/* Metadata Row */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-8">
                <div>
                    <Label htmlFor="inv-ref">Reference</Label>
                    <Input id="inv-ref" type="text" value={initialData?.invoice_reference || "Autogenerated"} disabled className="bg-gray-100" />
                </div>
                <div>
                    <Label htmlFor="inv-txn-date">Transaction Date</Label>
                    <Input
                        id="inv-txn-date"
                        type="date"
                        value={transactionDate}
                        onChange={(e) => setTransactionDate(e.target.value)}
                        disabled={restrictedMode}
                    />
                    {errors.transactionDate && <p className="text-xs text-red-500 mt-1">{errors.transactionDate}</p>}
                </div>
                <div>
                    <Label htmlFor="inv-due-date">Due Date</Label>
                    <Input
                        id="inv-due-date"
                        type="date"
                        value={dueDate}
                        onChange={(e) => setDueDate(e.target.value)}
                    />
                    {errors.dueDate && <p className="text-xs text-red-500 mt-1">{errors.dueDate}</p>}
                </div>
                <div>
                    <Label htmlFor="inv-rfq">RFQ/RFP Number</Label>
                    <Input
                        id="inv-rfq"
                        type="text"
                        value={rfqNumber}
                        onChange={(e) => setRfqNumber(e.target.value)}
                        placeholder="Enter..."
                    />
                </div>
                <div>
                    <Label htmlFor="inv-currency">Currency</Label>
                    <Select
                        id="inv-currency"
                        value={currency}
                        onChange={(e) => setCurrency(e.target.value)}
                        options={CURRENCY_OPTIONS}
                        disabled={restrictedMode}
                    />
                </div>
            </div>

            {/* Line Items */}
            <div className="mb-8">
                <h3 className="text-sm font-semibold text-gray-700 mb-3">Item Details</h3>
                {errors.lineItems && <p className="text-xs text-red-500 mb-2">{errors.lineItems}</p>}
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="bg-priori-purple text-white">
                                <th className="text-left px-3 py-2 rounded-tl-lg w-10">#</th>
                                <th className="text-left px-3 py-2">Description</th>
                                <th className="text-right px-3 py-2 w-24">Qty</th>
                                <th className="text-right px-3 py-2 w-28">Price</th>
                                <th className="text-center px-3 py-2 w-32">Tax</th>
                                <th className="text-right px-3 py-2 w-28">Total</th>
                                <th className="text-center px-3 py-2 rounded-tr-lg w-12"></th>
                            </tr>
                        </thead>
                        <tbody>
                            {lineItems.map((row, idx) => {
                                const lineTotal = calcLineTotal(row.quantity, row.unitPrice);
                                return (
                                    <tr key={row.key} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                                        <td className="px-3 py-2 text-gray-500">{idx + 1}</td>
                                        <td className="px-3 py-2">
                                            <Input
                                                type="text"
                                                value={row.description}
                                                onChange={(e) => updateRow(row.key, "description", e.target.value)}
                                                placeholder="Item description..."
                                                disabled={restrictedMode}
                                                className="text-sm py-1"
                                            />
                                        </td>
                                        <td className="px-3 py-2">
                                            <Input
                                                type="number"
                                                step="0.01"
                                                value={row.quantity}
                                                onChange={(e) => updateRow(row.key, "quantity", e.target.value)}
                                                disabled={restrictedMode}
                                                className="text-sm py-1 text-right"
                                            />
                                        </td>
                                        <td className="px-3 py-2">
                                            <Input
                                                type="number"
                                                step="0.01"
                                                value={row.unitPrice}
                                                onChange={(e) => updateRow(row.key, "unitPrice", e.target.value)}
                                                disabled={restrictedMode}
                                                className="text-sm py-1 text-right"
                                            />
                                        </td>
                                        <td className="px-3 py-2">
                                            <Select
                                                value={row.taxType}
                                                onChange={(e) => updateRow(row.key, "taxType", e.target.value)}
                                                options={TAX_OPTIONS}
                                                disabled={restrictedMode}
                                            />
                                        </td>
                                        <td className="px-3 py-2 text-right font-medium text-gray-800">
                                            {formatCurrency(lineTotal, currency)}
                                        </td>
                                        <td className="px-3 py-2 text-center">
                                            {!restrictedMode && lineItems.length > 1 && (
                                                <button
                                                    type="button"
                                                    onClick={() => removeRow(row.key)}
                                                    className="text-red-400 hover:text-red-600"
                                                >
                                                    <Trash2 size={16} />
                                                </button>
                                            )}
                                        </td>
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
                        className="mt-3 flex items-center gap-2 text-priori-purple text-sm font-medium hover:underline"
                    >
                        <Plus size={16} />
                        Add an item
                    </button>
                )}
            </div>

            {/* Financial Summary */}
            <div className="flex justify-end mb-8">
                <div className="w-80 space-y-3">
                    <div className="flex justify-between text-sm text-gray-600">
                        <span>Subtotal</span>
                        <span className="font-medium">{formatCurrency(totals.subtotal, currency)}</span>
                    </div>

                    {/* Discount */}
                    {!restrictedMode && (
                        <div className="space-y-2">
                            <Select
                                value={discountType}
                                onChange={(e) => setDiscountType(e.target.value)}
                                options={DISCOUNT_TYPE_OPTIONS}
                            />
                            {discountType === "amount" && (
                                <Input
                                    type="number"
                                    step="0.01"
                                    value={discountAmount}
                                    onChange={(e) => setDiscountAmount(e.target.value)}
                                    placeholder="Discount amount"
                                    className="text-sm"
                                />
                            )}
                            {discountType === "percentage" && (
                                <Input
                                    type="number"
                                    step="0.01"
                                    value={discountPercentage}
                                    onChange={(e) => setDiscountPercentage(e.target.value)}
                                    placeholder="Discount %"
                                    className="text-sm"
                                />
                            )}
                        </div>
                    )}

                    {totals.discountValue > 0 && (
                        <div className="flex justify-between text-sm text-red-500">
                            <span>Discount</span>
                            <span>-{formatCurrency(totals.discountValue, currency)}</span>
                        </div>
                    )}

                    <div className="flex justify-between text-sm text-gray-600">
                        <span>Tax</span>
                        <span className="font-medium">{formatCurrency(totals.taxTotal, currency)}</span>
                    </div>

                    <div className="border-t pt-3 flex justify-between text-base font-bold text-gray-800">
                        <span>Total Due</span>
                        <span>{formatCurrency(totals.totalDue, currency)}</span>
                    </div>
                </div>
            </div>

            {/* Notes */}
            <div className="mb-8">
                <Label htmlFor="inv-notes">Notes</Label>
                <textarea
                    id="inv-notes"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="Add notes here..."
                    rows={3}
                    className="w-full p-3 border border-gray-300 rounded-lg bg-gray-50 text-sm text-gray-800 focus:outline-none focus:border-priori-purple resize-none"
                />
            </div>

            {/* Bottom CTAs */}
            <div className="flex justify-end gap-3">
                <Button
                    variant="outline-secondary"
                    onClick={onCancel}
                    className="px-6 py-3"
                >
                    Cancel
                </Button>
                <Button
                    onClick={handleSubmit}
                    loading={isLoading}
                    className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg px-6 py-3"
                >
                    Save &amp; Continue
                </Button>
            </div>
        </div>
    );
}
