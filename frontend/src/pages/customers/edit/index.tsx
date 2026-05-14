import { CustomerDetailsForm } from "@/components/forms/CustomerDetailsForm";
import { getCustomer, updateCustomer } from "@/lib/customerApi";
import type { CustomerFormData } from "@/validations/customerSchema";
import { useEffect, useState, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";

export default function EditCustomerPage() {
    const { id } = useParams<{ id: string }>();
    const [isLoading, setIsLoading] = useState(false);
    const [isFetching, setIsFetching] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [initialData, setInitialData] = useState<Partial<CustomerFormData> | null>(null);
    const navigate = useNavigate();

    const fetchCustomer = useCallback(async () => {
        if (!id) return;
        try {
            setIsFetching(true);
            const data = await getCustomer(id);
            // Map backend customer to form data
            const customer = data.customer;
            setInitialData({
                customerType: customer.customer_type,
                companyName: customer.company_name || "",
                firstName: customer.first_name,
                lastName: customer.last_name,
                email: customer.email,
                phone: customer.phone.replace("+254", ""), // Remove prefix if it exists to match form UI
                website: customer.website || "",
                vatNumber: customer.vat_number || "",
                currency: customer.currency,
                address: customer.address || "",
                address2: customer.address2 || "",
                country: customer.country || "KE",
                province: customer.province || "",
                city: customer.city || "",
                postalCode: customer.postal_code || "",
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to fetch customer data");
        } finally {
            setIsFetching(false);
        }
    }, [id]);

    useEffect(() => {
        fetchCustomer();
    }, [fetchCustomer]);

    const handleCancel = () => {
        navigate("/customers");
    };

    const handleSave = async (data: CustomerFormData) => {
        if (!id) return;
        try {
            setIsLoading(true);
            setError(null);

            await updateCustomer(id, data);

            // Navigate back to customers list on success
            navigate("/customers");
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to update customer"
            );
        } finally {
            setIsLoading(false);
        }
    };

    if (isFetching) {
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
            {initialData && (
                <CustomerDetailsForm
                    initialData={initialData}
                    onCancel={handleCancel}
                    onSave={handleSave}
                    isLoading={isLoading}
                />
            )}
        </div>
    );
}
