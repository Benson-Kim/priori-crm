import { DocumentEditor, type DocumentData } from "@/components/documents/DocumentEditor";
import { useHeaderOverride } from "@/components/layout/default-layout";
import { useQuoteForm } from "./use-quote-form";

interface QuoteFormProps {
    quoteId?: string;
}

export function QuoteForm({ quoteId }: QuoteFormProps) {
    const { initialData, isLoading, error, handleSave, handleCancel, isRestricted } = useQuoteForm(quoteId);

    useHeaderOverride(quoteId ? initialData?.quote_number : undefined, "");

    if (quoteId && isLoading && !initialData) {
        return (
            <div className="flex items-center justify-center h-40 text-gray-400">
                Loading quote...
            </div>
        );
    }

    if (error && !initialData) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-2">
                <p className="text-red-500">{error}</p>
                <button
                    type="button"
                    onClick={handleCancel}
                    className="text-priori-purple hover:underline"
                >
                    Back to Quotes
                </button>
            </div>
        );
    }

    return (
        <div className="flex flex-col h-full font-sans">
            <div className="flex justify-end mb-6">
                <button
                    type="button"
                    className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
                >
                    Actions
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="m6 9 6 6 6-6" />
                    </svg>
                </button>
            </div>

            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
                    <p className="text-red-800 font-medium">{error}</p>
                </div>
            )}

            {(!quoteId || initialData) && (() => {
                const docData: DocumentData | undefined = initialData ? {
                    documentReference: initialData.quote_reference,
                    customerId: initialData.customer_id,
                    customer: initialData.customer as any,
                    transactionDate: initialData.transaction_date,
                    dueDate: initialData.due_date,
                    currency: initialData.currency,
                    rfqNumber: initialData.rfq_rfp_number || "",
                    notes: initialData.notes || "",
                    discountType: initialData.discount_type as any,
                    discountAmount: initialData.discount_amount,
                    discountPercentage: initialData.discount_percentage,
                    lineItems: initialData.line_items.map(li => ({
                        id: li.id,
                        itemName: li.item_name || li.description.split('\n')[0] || "",
                        description: li.description,
                        quantity: li.quantity,
                        unitPrice: li.unit_price,
                        taxType: li.tax_type
                    }))
                } : undefined;

                return (
                    <DocumentEditor
                        type="quote"
                        initialData={docData}
                        onSave={async (payload) => {
                            await handleSave({
                                customerId: payload.customerId,
                                transactionDate: payload.transactionDate,
                                dueDate: payload.dueDate,
                                currency: payload.currency,
                                rfqRfpNumber: payload.rfqNumber,
                                notes: payload.notes,
                                discountType: payload.discountType,
                                discountAmount: payload.discountAmount,
                                discountPercentage: payload.discountPercentage,
                                lineItems: payload.lineItems.map(item => ({
                                    itemName: item.itemName || item.description.split('\n')[0] || "Item",
                                    description: item.description,
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
