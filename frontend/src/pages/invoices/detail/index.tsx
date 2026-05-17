import { RecordPaymentModal } from "@/components/invoices/RecordPaymentModal";
import { useHeaderOverride } from "@/components/layout/default-layout";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import type { InvoiceResponse } from "@/lib/invoiceApi";
import {
    cancelInvoice,
    duplicateInvoice,
    getInvoice,
    markAsSent,
} from "@/lib/invoiceApi";
import { formatCurrency, formatDate } from "@/lib/utils";
import { ArrowLeft, Ban, CheckCircle, Copy, CreditCard, Download, Pencil, Save, Send, SquarePen } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

export function InvoiceDetailPage() {
    const { id } = useParams<{ id: string }>();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const [invoice, setInvoice] = useState<InvoiceResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showPaymentModal, setShowPaymentModal] = useState(false);

    useHeaderOverride(invoice?.invoice_number, "");

    const fetchInvoice = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        try {
            const data = await getInvoice(id);
            setInvoice(data);
        } catch (err) {
            console.error("[InvoiceDetail] Failed:", err);
            setError(err instanceof Error ? err.message : "Failed to load invoice");
        } finally {
            setIsLoading(false);
        }
    }, [id]);

    useEffect(() => { fetchInvoice(); }, [fetchInvoice]);

    // Handle action=record-payment in query params
    useEffect(() => {
        if (searchParams.get("action") === "record-payment" && invoice) {
            setShowPaymentModal(true);
        }
    }, [searchParams, invoice]);

    const handleMarkSent = async () => {
        if (!invoice) return;
        try {
            await markAsSent(invoice.id);
            fetchInvoice();
        } catch (err) {
            console.error("[InvoiceDetail] Mark sent failed:", err);
        }
    };

    const handleDuplicate = async () => {
        if (!invoice) return;
        try {
            const dup = await duplicateInvoice(invoice.id);
            navigate(`/invoices/${dup.id}/edit`);
        } catch (err) {
            console.error("[InvoiceDetail] Duplicate failed:", err);
        }
    };

    const handleCancel = async () => {
        if (!invoice) return;
        if (!confirm(`Cancel invoice ${invoice.invoice_reference}? This is irreversible.`)) return;
        try {
            await cancelInvoice(invoice.id);
            fetchInvoice();
        } catch (err) {
            console.error("[InvoiceDetail] Cancel failed:", err);
        }
    };

    if (isLoading) {
        return <div className="flex items-center justify-center h-40 text-gray-400">Loading invoice...</div>;
    }

    if (error || !invoice) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-2">
                <p className="text-red-500">{error || "Invoice not found"}</p>
                <Button onClick={() => navigate("/invoices")} className="text-priori-purple">Back to Invoices</Button>
            </div>
        );
    }

    const status = invoice.status.toLowerCase();

    // Build actions
    const actions = [];
    if (status === "draft" || status === "sent") {
        actions.push({ key: "edit", label: "Edit", icon: <Pencil size={16} />, onClick: () => navigate(`/invoices/${invoice.id}/edit`) });
    }
    if (status === "draft") {
        actions.push({ key: "mark-sent", label: "Mark as Sent", icon: <CheckCircle size={16} />, onClick: handleMarkSent });
    }
    if (status !== "canceled") {
        actions.push({ key: "send", label: "Send", icon: <Send size={16} />, onClick: () => alert("Email sending — coming soon") });
    }
    if (["sent", "overdue", "partial", "paid"].includes(status)) {
        actions.push({ key: "payment", label: "Record Payment", icon: <CreditCard size={16} />, onClick: () => setShowPaymentModal(true) });
    }
    actions.push({ key: "pdf", label: "Download PDF", icon: <Download size={16} />, onClick: () => alert("PDF generation — coming soon") });
    actions.push({ key: "duplicate", label: "Duplicate", icon: <Copy size={16} />, onClick: handleDuplicate });
    if (status !== "canceled") {
        actions.push({ key: "cancel", label: "Cancel Invoice", icon: <Ban size={16} />, onClick: handleCancel });
    }

    return (
        <div className="flex flex-col gap-6 font-sans">

            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <Button variant="ghost" onClick={() => navigate("/invoices")}>
                        <ArrowLeft size={20} />
                    </Button>
                </div>
                <Dropdown items={actions} className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors" />
            </div>

            <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden shadow-sm">

                {/* Top Section */}
                <div className="p-6">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        {/* Logo Column */}
                        <div className="flex flex-col items-start gap-3">
                            <img
                                src="/Logo Priori.svg"
                                className="w-full max-w-[150px] aspect-[150/44] object-contain"
                                alt="Priori Logo"
                            />
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
                        </div>

                        {/* Invoice To */}
                        <div className="flex flex-col gap-1">
                            <h2 className="text-[22px] font-black text-gray-800 tracking-wider mb-1 uppercase">INVOICE</h2>
                            <p className="text-sm text-gray-500 mb-1">To</p>

                            <div className="flex flex-col gap-1">
                                <p className="text-[16px] font-bold text-priori-purple">{invoice.customer?.display_name || invoice.customer_id}</p>
                                {invoice.customer && (
                                    <>
                                        {invoice.customer.address && (
                                            <p className="text-sm text-gray-600">{invoice.customer.address}</p>
                                        )}
                                        <p className="text-sm text-gray-600">{invoice.customer.phone}</p>
                                        <p className="text-sm text-gray-600">{invoice.customer.email}</p>
                                    </>
                                )}
                            </div>

                        </div>
                    </div>
                </div>


                {/* Metadata Row */}
                <div className="p-6 gap-6">
                    <div className="grid grid-cols-2 md:grid-cols-10 gap-6">
                        <div className="flex flex-col gap-2 md:col-span-2">
                            <label className="text-gray-500 font-medium w-full">Reference</label>
                            <p className="font-bold text-gray-800">{invoice?.invoice_reference || "Autogenerated"}</p>
                        </div>

                        <div className="flex flex-col gap-2 md:col-span-2">
                            <label className="text-gray-500 font-medium w-full">Transaction Date</label>
                            <span className="font-bold text-priori-purple whitespace-nowrap">{formatDate(invoice.transaction_date)}</span>
                        </div>

                        <div className="flex flex-col gap-2 md:col-span-2">
                            <label className="text-gray-500 font-medium w-full">Due Date</label>
                            <span className="font-bold text-priori-purple whitespace-nowrap">{formatDate(invoice.due_date)}</span>
                        </div>

                        <div className="flex flex-col gap-2 min-w-0 md:col-span-3">
                            <label className="text-gray-500 font-medium w-full">RFQ/RFP Number</label>
                            <span className="invisible font-bold truncate block">{invoice.rfq_number || '-'}</span>

                        </div>

                        <div className="flex flex-col gap-2 md:col-span-1">
                            <label className="text-gray-500 font-medium w-full">Currency</label>
                            <span className="font-bold text-priori-purple whitespace-nowrap">{invoice.currency}</span>

                        </div>
                    </div>
                </div>

                <div className="h-px bg-purple-25 w-full" />

                {/* Line Items */}
                <div className="p-6 flex flex-col gap-6">
                    <h3 className="text-[20px] leading-7.5 font-bold text-gray-800">Item Details</h3>

                    <div className="flex flex-col gap-3">
                        <table className="w-full text-[16px]">
                            <thead>
                                <tr className="bg-priori-purple text-white px-3 py-4 first:rounded-tl-lg last:rounded-tr-lg grid grid-cols-7">
                                    <th className="text-left px-3 font-bold leading-8 col-span-4">Item</th>
                                    <th className="text-center px-3 font-bold leading-8">Quantity</th>
                                    <th className="text-center px-3 font-bold leading-8">Price</th>
                                    <th className="text-center px-3 font-bold leading-8">Total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {invoice.line_items.map(item => (

                                    <tr key={item.id} className="group relative mb-3 grid grid-cols-7 gap-4 pt-2">
                                        <td className="align-left col-span-4">
                                            <p className="w-full bg-transparent px-3 py-4 text-gray-800 flex flex-col gap-2">
                                                <span className="font-bold">
                                                    {item.description.split('\n')[0] || ""}
                                                </span>
                                                <span className="font-normal">
                                                    {item.description}
                                                </span>
                                            </p>
                                        </td>
                                        <td className="align-top">
                                            <p className="w-full bg-transparent px-3 py-4 text-gray-800 text-center">
                                                {item.quantity}
                                            </p>
                                        </td>
                                        <td className="align-top">
                                            <p className="w-full bg-transparent px-3 py-4 text-gray-800 text-right">
                                                {formatCurrency(item.unit_price, "")}
                                            </p>
                                        </td>
                                        <td className="align-top">
                                            <p className="w-full bg-transparent px-3 py-4 text-gray-800 text-right ">
                                                {formatCurrency(item.line_total, "")}
                                            </p>
                                        </td>
                                    </tr>
                                )
                                )}
                            </tbody>
                        </table>


                        {/* Subtotals */}
                        <div className="w-full flex justify-end">
                            <div className="min-w-80 flex flex-col gap-4 text-gray-800">
                                {/* Subtotal */}
                                <div className="flex justify-between items-center font-bold">
                                    <span>Subtotal</span>
                                    <span>{formatCurrency(invoice.subtotal, invoice.currency)}</span>
                                </div>

                                {invoice.discount_type && (
                                    <div className="flex justify-between items-center text-sm text-gray-600">
                                        <span className="font-bold">Discount {invoice.discount_type === "percentage" ? `(${invoice.discount_percentage}%)` : ""}</span>
                                        <span className="font-medium text-red-500">-{formatCurrency(invoice.discount_amount ?? 0, invoice.currency)}</span>
                                    </div>
                                )}

                                <div className="h-px bg-purple-25 w-full opacity-50" />

                                {/* VAT Row */}
                                <div className="flex justify-between items-center text-gray-800">
                                    <span>VAT</span>
                                    <span>{formatCurrency(invoice.tax_total, invoice.currency)}</span>
                                </div>

                                <div className="h-px bg-purple-25 w-full opacity-50" />

                                {/* Total Due */}
                                <div className="flex justify-between items-center font-bold text-[16px] text-gray-900 mt-1">
                                    <span>Total Due</span>
                                    <span>{formatCurrency(invoice.total_due, invoice.currency)}</span>
                                </div>

                                <div className="h-px bg-purple-25 w-full opacity-50" />

                                {invoice.amount_paid > 0 && (
                                    <>
                                        <div className="flex justify-between items-center font-bold text-[16px] text-gray-900 mt-1">
                                            <span>Amount Paid</span>
                                            <span>{formatCurrency(invoice.amount_paid, invoice.currency)}</span>
                                        </div>
                                        <div className="flex justify-between items-center font-bold text-[16px] text-gray-900 mt-1">
                                            <span>Balance Due</span>
                                            <span>{formatCurrency(invoice.balance_due, invoice.currency)}</span>
                                        </div>
                                    </>
                                )}
                            </div>
                        </div>

                    </div>
                </div>

                {/* Notes */}
                <div className="p-8 pt-6">
                    <label className="block text-[16px] font-bold text-gray-800 mb-3">Notes</label>
                    <div className="w-full p-4 text-[16px]"> {invoice.notes}</div>
                </div>

            </div>

            {/* Footer Actions */}
            <div className="flex justify-end items-center gap-4 mt-2">
                <Button
                    variant="outline"
                    onClick={() => alert("Preview not implemented")}
                    className=""
                >
                    Preview
                </Button>
                <Button
                    variant="primary"
                    disabled={isLoading}
                    className="flex items-center gap-2"
                >
                    <Save size={18} /> Save &amp; Continue
                </Button>
            </div>

            <RecordPaymentModal
                isOpen={showPaymentModal}
                onClose={() => {
                    setShowPaymentModal(false);
                    // Clear the query param
                    navigate(`/invoices/${invoice.id}`, { replace: true });
                }}
                invoice={invoice}
                onSuccess={() => {
                    setShowPaymentModal(false);
                    navigate(`/invoices/${invoice.id}`, { replace: true });
                    fetchInvoice();
                }}
            />
        </div>
    );
}
