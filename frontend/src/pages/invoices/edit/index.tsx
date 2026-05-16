import { InvoiceForm } from "@/components/forms/InvoiceForm";
import { getInvoice, updateInvoice, type InvoiceCreatePayload, type InvoiceResponse } from "@/lib/invoiceApi";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

export default function EditInvoicePage() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const [invoice, setInvoice] = useState<InvoiceResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const fetchInvoice = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        try {
            const data = await getInvoice(id);
            console.log("[EditInvoice] Loaded invoice:", data.invoice_reference, "status:", data.status);

            // Check if editable
            const status = data.status.toLowerCase();
            if (!["draft", "sent"].includes(status)) {
                console.log("[EditInvoice] Not editable, redirecting to detail");
                navigate(`/invoices/${id}`, { replace: true });
                return;
            }

            setInvoice(data);
        } catch (err) {
            console.error("[EditInvoice] Failed to load:", err);
            setError(err instanceof Error ? err.message : "Failed to load invoice");
        } finally {
            setIsLoading(false);
        }
    }, [id, navigate]);

    useEffect(() => { fetchInvoice(); }, [fetchInvoice]);

    const handleCancel = () => {
        navigate(id ? `/invoices/${id}` : "/invoices");
    };

    const handleSave = async (data: InvoiceCreatePayload) => {
        if (!id || !invoice) return;
        try {
            setIsSaving(true);
            setError(null);

            const updated = await updateInvoice(id, data, invoice.version);
            console.log("[EditInvoice] Updated invoice:", updated.invoice_number);

            navigate(`/invoices/${id}`);
        } catch (err) {
            const message = err instanceof Error ? err.message : "Failed to update invoice";
            console.error("[EditInvoice] Error:", message);
            setError(message);
        } finally {
            setIsSaving(false);
        }
    };

    if (isLoading) {
        return <div className="flex items-center justify-center h-40 text-gray-400">Loading invoice...</div>;
    }

    if (error && !invoice) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-2">
                <p className="text-red-500">{error}</p>
                <button type="button" onClick={() => navigate("/invoices")} className="text-priori-purple hover:underline">
                    Back to Invoices
                </button>
            </div>
        );
    }

    if (!invoice) return null;

    const isRestricted = invoice.status.toLowerCase() === "sent";

    return (
        <div className="flex flex-col h-full px-4 mx-auto font-sans">
            <div className="flex justify-end mb-6">
                <button type="button" className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors">
                    Actions <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
                </button>
            </div>

            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
                    <p className="text-red-800 font-medium">{error}</p>
                </div>
            )}
            <InvoiceForm
                initialData={invoice}
                onCancel={handleCancel}
                onSave={handleSave}
                isLoading={isSaving}
                restrictedMode={isRestricted}
            />
        </div>
    );
}
