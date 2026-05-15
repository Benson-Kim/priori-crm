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
            console.log("[AddInvoice] Created invoice:", invoice.invoice_number, invoice.id);

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
        <div>
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