import { CustomerDetailsForm } from "@/components/forms/CustomerDetailsForm";
import { LoadingState } from "@/components/ui/LoadingState";
import { useCustomerForm } from "@/hooks/use-customer-form";

interface CustomerFormProps {
    customerId?: string;
}

export function CustomerForm({ customerId }: CustomerFormProps) {
    const {
        initialData,
        isLoading,
        error,
        handleSave,
        handleCancel
    } = useCustomerForm(customerId);

    // Show loading state while fetching customer data for edit mode
    if (customerId && isLoading && !initialData) {
        return <LoadingState message="Loading customer details..." className="h-64" />;
    }

    return (
        <div className="">
            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
                    <p className="text-red-800 font-medium">{error}</p>
                </div>
            )}
            {(!customerId || initialData) && (
                <CustomerDetailsForm
                    initialData={initialData || undefined}
                    onCancel={handleCancel}
                    onSave={handleSave}
                    isLoading={isLoading}
                />
            )}
        </div>
    );
}