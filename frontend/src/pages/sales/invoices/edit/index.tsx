import { DocumentEditor } from "@/components/documents/DocumentEditor";
import { LoadingState } from "@/components/ui/LoadingState";
import { useInvoiceForm } from "@/hooks/use-invoice-form";
import type { CurrencyOption } from "@/lib/constants";
import { useNavigate, useParams } from "react-router-dom";

export default function EditInvoicePage() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();

    const {
        initialData,
        isFetching,
        isLoading,
        isRestricted,
        error,
        handleSave,
    } = useInvoiceForm(id);

    if (isFetching) {
        return <LoadingState message="Loading invoice..." />;
    }

    if (error && !initialData) {
        return (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                {error}
            </div>
        );
    }

    return (
        <>
            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    {error}
                </div>
            )}
            <DocumentEditor
                type="invoice"
                initialData={
                    initialData
                        ? {
                            documentReference: initialData.invoice_reference,
                            customerId: initialData.customer_id,
                            customer: initialData.customer,
                            transactionDate: initialData.transaction_date,
                            dueDate: initialData.due_date,
                            currency: initialData.currency as CurrencyOption,
                            // rfqNumber: initialData.rfq_number ?? undefined,
                            notes: initialData.notes ?? undefined,
                            discountType: initialData.discount_type as
                                | "amount"
                                | "percentage"
                                | null,
                            discountAmount: initialData.discount_amount == null ? undefined : Number(initialData.discount_amount),
                            discountPercentage: initialData.discount_percentage == null ? undefined : Number(initialData.discount_percentage),
                            lineItems: (initialData.line_items ?? []).map((li) => ({
                                id: li.id,
                                itemName: li.item_name,
                                description: li.description,
                                quantity: Number(li.quantity),
                                unitPrice: Number(li.unit_price),
                                taxType: li.tax_type,
                            })),
                        }
                        : undefined
                }
                onSave={handleSave}
                isLoading={isLoading}
                restrictedMode={isRestricted}
                onPreview={() => navigate(`/invoices/${id}`)}
            />
        </>
    );
}