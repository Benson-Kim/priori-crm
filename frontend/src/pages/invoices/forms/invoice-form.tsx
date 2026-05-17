import { DocumentEditor, type DocumentData } from "@/components/documents/DocumentEditor";
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
        <div className="flex flex-col h-full font-sans">
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

            {(!invoiceId || initialData) && (() => {
                const docData: DocumentData | undefined = initialData ? {
                    documentReference: initialData.invoice_reference,
                    customerId: initialData.customer_id,
                    customer: initialData.customer,
                    transactionDate: initialData.transaction_date,
                    dueDate: initialData.due_date,
                    currency: initialData.currency,
                    rfqNumber: initialData.rfq_number || "",
                    notes: initialData.notes || "",
                    discountType: initialData.discount_type as any,
                    discountAmount: initialData.discount_amount,
                    discountPercentage: initialData.discount_percentage,
                    lineItems: initialData.line_items.map(li => ({
                        id: li.id,
                        itemName: li.description.split('\n')[0] || "",
                        description: li.description,
                        quantity: li.quantity,
                        unitPrice: li.unit_price,
                        taxType: li.tax_type
                    }))
                } : undefined;

                return (
                    <DocumentEditor
                        type="invoice"
                        initialData={docData}
                        onSave={async (payload) => {
                            await handleSave({
                                customerId: payload.customerId,
                                transactionDate: payload.transactionDate,
                                dueDate: payload.dueDate,
                                currency: payload.currency,
                                rfqNumber: payload.rfqNumber,
                                notes: payload.notes,
                                discountType: payload.discountType,
                                discountAmount: payload.discountAmount,
                                discountPercentage: payload.discountPercentage,
                                lineItems: payload.lineItems.map(item => ({
                                    description: item.description || item.itemName || "",
                                    quantity: item.quantity,
                                    unitPrice: item.unitPrice,
                                    taxType: item.taxType
                                }))
                            });
                        }}
                        isLoading={isLoading}
                        restrictedMode={isRestricted}
                    />
                );
            })()}
        </div>
    );
}


