import type { DocumentPayload } from "@/components/documents/DocumentEditor";
import { createInvoice, getInvoice, updateInvoice, type InvoiceCreatePayload, type InvoiceResponse } from "@/services/invoiceApi";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface UseInvoiceFormReturn {
    initialData: InvoiceResponse | null;
    isLoading: boolean;
    isFetching: boolean;
    error: string | null;
    isRestricted: boolean;
    handleSave: (payload: DocumentPayload) => Promise<void>;
    handleCancel: () => void;
}

export function useInvoiceForm(invoiceId?: string): UseInvoiceFormReturn {
    const [isFetching, setIsFetching] = useState(!!invoiceId);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [initialData, setInitialData] = useState<InvoiceResponse | null>(null);
    const navigate = useNavigate();

    const fetchInvoice = useCallback(async () => {
        if (!invoiceId) return;

        try {
            setIsFetching(true);
            const data = await getInvoice(invoiceId);

            // Check if editable
            if (!data.is_editable) {
                navigate(`/invoices/${invoiceId}`, { replace: true });
                return;
            }

            setInitialData(data);
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to load invoice"
            );
        } finally {
            setIsFetching(false);
        }
    }, [invoiceId, navigate]);

    useEffect(() => {
        if (invoiceId) void (async () => { await fetchInvoice(); })();
    }, [invoiceId, fetchInvoice]);

    const handleCancel = () => {
        navigate(invoiceId ? `/invoices/${invoiceId}` : "/invoices");
    };

    const handleSave = async (payload: DocumentPayload) => {
        try {
            setIsLoading(true);
            setError(null);

            const invoicePayload: InvoiceCreatePayload = {
                customerId: payload.customerId,
                transactionDate: payload.transactionDate,
                dueDate: payload.dueDate,
                currency: payload.currency,
                vatEnabled: payload.vatEnabled,
                vatRate: payload.vatRate,
                vatComplianceRef: payload.vatComplianceRef,
                lineItems: payload.lineItems.map((li) => ({
                    itemName: li.itemName,
                    description: li.description,
                    quantity: li.quantity,
                    unitPrice: li.unitPrice,
                    taxType: li.taxType,
                })),
                // rfqNumber: payload.rfqNumber,
                notes: payload.notes,
                discountType: payload.discountType,
                discountAmount: payload.discountAmount,
                discountPercentage: payload.discountPercentage,
            };

            if (invoiceId && initialData) {
                await updateInvoice(invoiceId, invoicePayload, initialData.version);
                navigate(`/invoices/${invoiceId}`);
            } else {
                const invoice = await createInvoice(invoicePayload);
                navigate(`/invoices/${invoice.id}`);
            }
        } catch (err) {
            setError(
                err instanceof Error
                    ? err.message
                    : invoiceId
                        ? "Failed to update invoice"
                        : "Failed to create invoice"
            );
            throw err;
        } finally {
            setIsLoading(false);
        }
    };

    return {
        initialData,
        isFetching,
        isLoading,
        error,
        isRestricted: initialData?.status.toLowerCase() === "sent",
        handleSave,
        handleCancel,
    };
}
