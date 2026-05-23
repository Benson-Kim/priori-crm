import { DocumentEditor } from "@/components/documents/DocumentEditor";
import { useQuoteForm } from "@/hooks/use-quote-form";
import { useNavigate } from "react-router-dom";

export default function AddQuotePage() {
    const { handleSave, isLoading, error } = useQuoteForm();
    const navigate = useNavigate();

    return (
        <>
            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    {error}
                </div>
            )}
            <DocumentEditor
                type="quote"
                onSave={handleSave}
                isLoading={isLoading}
                onPreview={() => navigate("/quote/preview")}
            />
        </>
    );
}