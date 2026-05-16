import { InvoiceForm } from "@/components/forms/InvoiceForm";
import { createInvoice, type InvoiceCreatePayload } from "@/lib/invoiceApi";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

export default function AddInvoicePage() {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const navigate = useNavigate();

    const handleCancel = () => {
        navigate("/invoices");
    };

    const handleSave = async (data: InvoiceCreatePayload) => {
        try {
            setIsLoading(true);
            setError(null);

            const invoice = await createInvoice(data);

            // Redirect to detail page
            navigate(`/invoices/${invoice.id}`);
        } catch (err) {
            const message = err instanceof Error ? err.message : "Failed to create invoice";
            console.error("[AddInvoice] Error:", message);
            setError(message);
        } finally {
            setIsLoading(false);
        }
    };

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
                onCancel={handleCancel}
                onSave={handleSave}
                isLoading={isLoading}
            />
        </div>
    );
}