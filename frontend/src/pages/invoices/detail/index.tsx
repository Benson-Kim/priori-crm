import { RecordPaymentModal } from "@/components/invoices/RecordPaymentModal";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import {
    cancelInvoice,
    duplicateInvoice,
    getInvoice,
    markAsSent,
    type InvoiceResponse,
} from "@/lib/invoiceApi";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    ArrowLeft,
    Ban,
    CheckCircle,
    Copy,
    CreditCard,
    Download,
    Pencil,
    Send,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

export default function InvoiceDetailPage() {
    const { id } = useParams<{ id: string }>();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const [invoice, setInvoice] = useState<InvoiceResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showPaymentModal, setShowPaymentModal] = useState(false);

    const fetchInvoice = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        try {
            const data = await getInvoice(id);
            console.log("[InvoiceDetail] Fetched invoice:", data);
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
            console.log("[InvoiceDetail] Marked as sent");
            fetchInvoice();
        } catch (err) {
            console.error("[InvoiceDetail] Mark sent failed:", err);
        }
    };

    const handleDuplicate = async () => {
        if (!invoice) return;
        try {
            const dup = await duplicateInvoice(invoice.id);
            console.log("[InvoiceDetail] Duplicated:", dup.invoice_number);
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
            console.log("[InvoiceDetail] Cancelled");
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
    const badgeVariant = (status as "draft" | "sent" | "paid" | "partial" | "overdue" | "canceled") || "draft";

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
        <div className="flex flex-col gap-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <button type="button" onClick={() => navigate("/invoices")} className="text-gray-500 hover:text-gray-700">
                        <ArrowLeft size={20} />
                    </button>
                    <h2 className="text-2xl font-bold text-gray-800">{invoice.invoice_reference}</h2>
                    <Badge variant={badgeVariant}>
                        {invoice.is_overdue ? `Overdue (${invoice.days_overdue} days)` : status.charAt(0).toUpperCase() + status.slice(1)}
                    </Badge>
                </div>
                <Dropdown items={actions} className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors" />
            </div>

            {/* Main Content Card */}
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
                    {/* Recipient */}
                    <div className="text-right md:text-left">
                        <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Invoice</p>
                        <h4 className="text-sm font-medium text-gray-600 mb-1">To</h4>
                        <p className="text-sm font-semibold text-gray-800">{invoice.customer?.display_name || invoice.customer_id}</p>
                        {invoice.customer && (
                            <>
                                <p className="text-sm text-gray-500">{invoice.customer.email}</p>
                                <p className="text-sm text-gray-500">{invoice.customer.phone}</p>
                            </>
                        )}
                    </div>
                </div>

                {/* Metadata Row */}
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-8 p-4 bg-gray-50 rounded-xl">
                    <div>
                        <p className="text-xs text-gray-400 uppercase">Reference</p>
                        <p className="text-sm font-medium text-gray-800">{invoice.invoice_reference}</p>
                    </div>
                    <div>
                        <p className="text-xs text-gray-400 uppercase">Transaction Date</p>
                        <p className="text-sm font-medium text-gray-800">{formatDate(invoice.transaction_date)}</p>
                    </div>
                    <div>
                        <p className="text-xs text-gray-400 uppercase">Due Date</p>
                        <p className="text-sm font-medium text-gray-800">{formatDate(invoice.due_date)}</p>
                    </div>
                    <div>
                        <p className="text-xs text-gray-400 uppercase">RFQ/RFP</p>
                        <p className="text-sm font-medium text-gray-800">{invoice.rfq_number || "—"}</p>
                    </div>
                    <div>
                        <p className="text-xs text-gray-400 uppercase">Currency</p>
                        <p className="text-sm font-medium text-gray-800">{invoice.currency}</p>
                    </div>
                </div>

                {/* Line Items Table */}
                <div className="mb-8">
                    <h3 className="text-sm font-semibold text-gray-700 mb-3">Item Details</h3>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-priori-purple text-white">
                                    <th className="text-left px-4 py-3 rounded-tl-lg">Item</th>
                                    <th className="text-left px-4 py-3">Description</th>
                                    <th className="text-right px-4 py-3">Quantity</th>
                                    <th className="text-right px-4 py-3">Price</th>
                                    <th className="text-right px-4 py-3">Tax</th>
                                    <th className="text-right px-4 py-3 rounded-tr-lg">Total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {invoice.line_items.map((item, idx) => (
                                    <tr key={item.id} className={idx % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                                        <td className="px-4 py-3 font-medium text-gray-800">{idx + 1}</td>
                                        <td className="px-4 py-3 text-gray-600">{item.description}</td>
                                        <td className="px-4 py-3 text-right text-gray-600">{item.quantity}</td>
                                        <td className="px-4 py-3 text-right text-gray-600">{formatCurrency(item.unit_price, invoice.currency)}</td>
                                        <td className="px-4 py-3 text-right text-gray-500 text-xs">{item.tax_type}</td>
                                        <td className="px-4 py-3 text-right font-medium text-gray-800">{formatCurrency(item.line_total, invoice.currency)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>

                {/* Financial Summary */}
                <div className="flex justify-end mb-8">
                    <div className="w-72 space-y-3">
                        <div className="flex justify-between text-sm text-gray-600">
                            <span>Subtotal</span>
                            <span className="font-medium">{formatCurrency(invoice.subtotal, invoice.currency)}</span>
                        </div>
                        {invoice.discount_type && (
                            <div className="flex justify-between text-sm text-gray-600">
                                <span>Discount {invoice.discount_type === "percentage" ? `(${invoice.discount_percentage}%)` : ""}</span>
                                <span className="font-medium text-red-500">-{formatCurrency(invoice.discount_amount ?? 0, invoice.currency)}</span>
                            </div>
                        )}
                        <div className="flex justify-between text-sm text-gray-600">
                            <span>Tax</span>
                            <span className="font-medium">{formatCurrency(invoice.tax_total, invoice.currency)}</span>
                        </div>
                        <div className="border-t pt-3 flex justify-between text-base font-bold text-gray-800">
                            <span>Total Due</span>
                            <span>{formatCurrency(invoice.total_due, invoice.currency)}</span>
                        </div>
                        {invoice.amount_paid > 0 && (
                            <>
                                <div className="flex justify-between text-sm text-emerald-600">
                                    <span>Amount Paid</span>
                                    <span className="font-medium">{formatCurrency(invoice.amount_paid, invoice.currency)}</span>
                                </div>
                                <div className="flex justify-between text-sm font-semibold text-gray-800">
                                    <span>Balance Due</span>
                                    <span>{formatCurrency(invoice.balance_due, invoice.currency)}</span>
                                </div>
                            </>
                        )}
                    </div>
                </div>

                {/* Notes */}
                {invoice.notes && (
                    <div className="mb-8">
                        <h3 className="text-sm font-semibold text-gray-700 mb-2">Notes</h3>
                        <div className="p-4 bg-gray-50 rounded-xl text-sm text-gray-600 whitespace-pre-wrap">{invoice.notes}</div>
                    </div>
                )}

                {/* Payment History */}
                {invoice.payments.length > 0 && (
                    <div>
                        <h3 className="text-sm font-semibold text-gray-700 mb-3">Payment History</h3>
                        <div className="space-y-2">
                            {invoice.payments.map((p) => (
                                <div key={p.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg text-sm">
                                    <div>
                                        <span className="font-medium text-gray-800">{formatCurrency(p.amount, invoice.currency)}</span>
                                        <span className="text-gray-400 mx-2">•</span>
                                        <span className="text-gray-500">{p.payment_method}</span>
                                        {p.reference && <span className="text-gray-400 ml-2">Ref: {p.reference}</span>}
                                    </div>
                                    <span className="text-gray-400">{formatDate(p.payment_date)}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            {/* Bottom CTAs */}
            <div className="flex justify-end gap-3">
                {invoice.is_editable && (
                    <Button
                        onClick={() => navigate(`/invoices/${invoice.id}/edit`)}
                        className="bg-priori-purple hover:bg-priori-purple/90 text-white rounded-lg px-6 py-3"
                    >
                        <Pencil size={16} className="mr-2" />
                        Edit Invoice
                    </Button>
                )}
            </div>

            {/* Record Payment Modal */}
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
