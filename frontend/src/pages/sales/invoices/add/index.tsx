import { DocumentEditor } from "@/components/documents/DocumentEditor";
import { useInvoiceForm } from "@/hooks/use-invoice-form";
import { useNavigate } from "react-router-dom";

export default function AddInvoicePage() {
    const { handleSave, isLoading, error } = useInvoiceForm();
    const navigate = useNavigate();

    return (
        <>
            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    {error}
                </div>
            )}
            <DocumentEditor
                type="invoice"
                onSave={handleSave}
                isLoading={isLoading}
                onPreview={() => navigate("/invoices/preview")}
            />
        </>
    );
}