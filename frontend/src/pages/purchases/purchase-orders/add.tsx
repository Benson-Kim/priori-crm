import { PurchaseOrderEditor } from "@/components/documents/PurchaseOrderEditor";
import { usePurchaseOrderForm } from "@/hooks/use-purchase-order-form";

export default function AddPurchaseOrderPage() {
    const { handleSave, isLoading, error } = usePurchaseOrderForm();

    return (
        <>
            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    {error}
                </div>
            )}
            <PurchaseOrderEditor
                onSave={handleSave}
                isLoading={isLoading}
            />
        </>
    );
}
