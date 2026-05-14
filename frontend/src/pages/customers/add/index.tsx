import { CustomerDetailsForm } from "@/components/forms/CustomerDetailsForm";
import { createCustomer } from "@/lib/customerApi";
import type { CustomerFormData } from "@/validations/customerSchema";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

export default function AddCustomerPage() {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const navigate = useNavigate();

    const handleCancel = () => {
        navigate("/customers");
    };

    const handleSave = async (data: CustomerFormData) => {
        try {
            setIsLoading(true);
            setError(null);

            await createCustomer(data);

            // Navigate back to customers list on success
            navigate("/customers");
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to add customer"
            );
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="">
            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
                    <p className="text-red-800 font-medium">{error}</p>
                </div>
            )}
            <CustomerDetailsForm
                onCancel={handleCancel}
                onSave={handleSave}
                isLoading={isLoading}
            />
        </div>
    );
}