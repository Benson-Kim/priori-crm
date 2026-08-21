import { createCustomer, getCustomer, updateCustomer } from "@/services/customerApi";
import type { CustomerFormData } from "@/validations/customerSchema";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface UseCustomerFormReturn {
    initialData: Partial<CustomerFormData> | null;
    isLoading: boolean;
    isFetching: boolean;
    error: string | null;
    handleSave: (data: CustomerFormData) => Promise<void>;
    handleCancel: () => void;
}

export function useCustomerForm(customerId?: string): UseCustomerFormReturn {
    const [isLoading, setIsLoading] = useState(false);
    const [isFetching, setIsFetching] = useState(!!customerId);
    const [error, setError] = useState<string | null>(null);
    const [initialData, setInitialData] = useState<Partial<CustomerFormData> | null>(null);
    const [customerVersion, setCustomerVersion] = useState<number | null>(null);
    const navigate = useNavigate();

    const fetchCustomer = useCallback(async () => {
        if (!customerId) return;

        try {
            setIsFetching(true);
            const data = await getCustomer(customerId);
            // Map backend customer to form data
            const customer = data.customer;
            setCustomerVersion(customer.version);
            setInitialData({
                customerType: customer.customer_type,
                companyName: customer.company_name || "",
                firstName: customer.first_name || "",
                lastName: customer.last_name || "",
                email: customer.email || "",
                // Optional: strip the country prefix only when a number is
                // on file. Calling .replace() on an absent phone throws.
                phone: (customer.phone || "").replace(/^\+?254/, "").trim(),
                website: customer.website || "",
                vatNumber: customer.vat_number || "",
                currency: customer.currency,
                address: customer.address || "",
                address2: customer.address2 || "",
                country: customer.country || "KE",
                city: customer.city || "",
                postalCode: customer.postal_code || "",
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to fetch customer data");
        } finally {
            setIsFetching(false);
        }
    }, [customerId]);

    useEffect(() => {
        if (customerId) {
            void (async () => { await fetchCustomer(); })();
        }
    }, [customerId, fetchCustomer]);

    const handleCancel = () => {
        navigate("/customers");
    };

    const handleSave = async (data: CustomerFormData) => {
        try {
            setIsLoading(true);
            setError(null);

            if (customerId) {
                // Update existing customer
                if (customerVersion === null) throw new Error("Customer version missing");
                await updateCustomer(customerId, customerVersion, data);
            } else {
                // Create new customer
                await createCustomer(data);
            }

            // Navigate back to customers list on success
            navigate("/customers");
        } catch (err) {
            setError(
                err instanceof Error
                    ? err.message
                    : customerId
                        ? "Failed to update customer"
                        : "Failed to add customer"
            );
        } finally {
            setIsLoading(false);
        }
    };

    return {
        initialData,
        isLoading,
        isFetching,
        error,
        handleSave,
        handleCancel,
    };
}