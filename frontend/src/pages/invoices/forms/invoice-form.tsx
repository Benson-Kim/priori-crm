import { InvoiceForm as InvoiceFormComponent } from "@/components/forms/InvoiceForm";
import { useHeaderOverride } from "@/components/layout/default-layout";
import { Button } from "@/components/ui/Button";
import { ChevronDown } from "lucide-react";
import { useInvoiceForm } from "./use-invoice-form";

interface InvoiceFormProps {
    invoiceId?: string;
}

export function InvoiceForm({ invoiceId }: InvoiceFormProps) {
    const { initialData, isLoading, error, handleSave, handleCancel, isRestricted } = useInvoiceForm(invoiceId);

    useHeaderOverride(invoiceId ? initialData?.invoice_number : undefined, "");

    // Show loading state while fetching invoice data for edit mode
    if (invoiceId && isLoading && !initialData) {
        return (
            <div className="flex items-center justify-center h-40 text-gray-400">
                Loading invoice...
            </div>
        );
    }

    // Show error state if loading failed and no data
    if (error && !initialData) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-2">
                <p className="text-red-500">{error}</p>
                <button
                    type="button"
                    onClick={handleCancel}
                    className="text-priori-purple hover:underline"
                >
                    Back to Invoices
                </button>
            </div>
        );
    }

    return (
        <div className="flex flex-col h-full px-4 mx-auto font-sans">
            <div className="flex justify-end mb-6">
                <Button variant="outline" className="flex items-center justify-between">
                    Actions
                    <ChevronDown />
                </Button>
            </div>

            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
                    <p className="text-red-800 font-medium">{error}</p>
                </div>
            )}

            {(!invoiceId || initialData) && (
                <InvoiceFormComponent
                    initialData={initialData || undefined}
                    onCancel={handleCancel}
                    onSave={handleSave}
                    isLoading={isLoading}
                    restrictedMode={isRestricted}
                />
            )}
        </div>
    );
}


