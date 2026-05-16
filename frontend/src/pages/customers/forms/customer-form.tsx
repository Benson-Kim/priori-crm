import { CustomerDetailsForm } from "@/components/forms/CustomerDetailsForm";
import { useCustomerForm } from "./use-customer-form";

interface CustomerFormProps {
    customerId?: string;
}

export function CustomerForm({ customerId }: CustomerFormProps) {
    const { initialData, isLoading, error, handleSave, handleCancel } = useCustomerForm(customerId);

    // Show loading state while fetching customer data for edit mode
    if (customerId && isLoading && !initialData) {
        return (
            <div className="flex items-center justify-center h-64 text-gray-400">
                Loading customer details...
            </div>
        );
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