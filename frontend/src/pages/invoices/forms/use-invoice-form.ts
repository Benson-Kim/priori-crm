import { createInvoice, getInvoice, updateInvoice, type InvoiceCreatePayload, type InvoiceResponse } from "@/lib/invoiceApi";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface UseInvoiceFormReturn {
    initialData: InvoiceResponse | null;
    isLoading: boolean;
    error: string | null;
    handleSave: (data: InvoiceCreatePayload) => Promise<void>;
    handleCancel: () => void;
    isRestricted: boolean;
}

export function useInvoiceForm(invoiceId?: string): UseInvoiceFormReturn {
    const [isLoading, setIsLoading] = useState(false);
    const [isFetching, setIsFetching] = useState(!!invoiceId);
    const [error, setError] = useState<string | null>(null);
    const [initialData, setInitialData] = useState<InvoiceResponse | null>(null);
    const navigate = useNavigate();

    const fetchInvoice = useCallback(async () => {
        if (!invoiceId) return;
        
        try {
            setIsFetching(true);
            const data = await getInvoice(invoiceId);

            // Check if editable
            const status = data.status.toLowerCase();
            if (!["draft", "sent"].includes(status)) {
                navigate(`/invoices/${invoiceId}`, { replace: true });
                return;
            }

            setInitialData(data);
        } catch (err) {
            console.error("[useInvoiceForm] Failed to load:", err);
            setError(err instanceof Error ? err.message : "Failed to load invoice");
        } finally {
            setIsFetching(false);
        }
    }, [invoiceId, navigate]);

    useEffect(() => {
        if (invoiceId) {
            fetchInvoice();
        }
    }, [invoiceId, fetchInvoice]);

    const handleCancel = () => {
        if (invoiceId) {
            navigate(`/invoices/${invoiceId}`);
        } else {
            navigate("/invoices");
        }
    };

    const handleSave = async (data: InvoiceCreatePayload) => {
        try {
            setIsLoading(true);
            setError(null);

            if (invoiceId && initialData) {
                // Update existing invoice
                await updateInvoice(invoiceId, data, initialData.version);
                navigate(`/invoices/${invoiceId}`);
            } else {
                // Create new invoice and Redirect to detail page
                const invoice = await createInvoice(data);
                navigate(`/invoices/${invoice.id}`);
            }
        } catch (err) {
            const message = err instanceof Error 
                ? err.message 
                : invoiceId 
                    ? "Failed to update invoice" 
                    : "Failed to create invoice";
            console.error("[useInvoiceForm] Error:", message);
            setError(message);
        } finally {
            setIsLoading(false);
        }
    };

    const isRestricted = initialData?.status.toLowerCase() === "sent";

    return {
        initialData,
        isLoading: isLoading || isFetching,
        error,
        handleSave,
        handleCancel,
        isRestricted,
    };
}
