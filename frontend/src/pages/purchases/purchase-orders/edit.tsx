import { PurchaseOrderEditor } from "@/components/documents/PurchaseOrderEditor";
import { LoadingState } from "@/components/ui/LoadingState";
import { usePurchaseOrderForm } from "@/hooks/use-purchase-order-form";
import type { PurchaseOrderLineItem } from "@/services/purchaseOrderApi";
import { useParams } from "react-router-dom";

export default function EditPurchaseOrderPage() {
    const { id } = useParams<{ id: string }>();
    const { initialData, isFetching, isLoading, error, isRestricted, handleSave } =
        usePurchaseOrderForm(id);

    if (isFetching) {
        return <LoadingState message="Loading purchase order details..." />;
    }

    if (error || !initialData) {
        return (
            <div className="p-8 text-center text-red-500">
                {error ?? "Purchase order not found"}
            </div>
        );
    }

    return (
        <PurchaseOrderEditor
            initialData={{
                poReference: initialData.po_reference,
                vendorId: initialData.vendor_id,
                vendor: initialData.vendor,
                orderDate: initialData.order_date,
                deliveryDate: initialData.delivery_date,
                notes: initialData.notes || undefined,
                termsAndConditions: initialData.terms_and_conditions,
                lineItems: (initialData.line_items ?? []).map((li: PurchaseOrderLineItem) => ({
                    id: li.id,
                    itemName: li.item_name,
                    description: li.description,
                    quantity: Number(li.quantity),
                    unitPrice: Number(li.unit_price),
                    taxType: li.tax_type,
                })),
            }}
            onSave={handleSave}
            isLoading={isLoading}
            restrictedMode={isRestricted}
        />
    );
}
