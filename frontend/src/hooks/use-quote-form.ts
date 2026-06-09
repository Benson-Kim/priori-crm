/**
 * useQuoteForm — manages state for create and edit quote flows.
 */

import type { DocumentPayload } from "@/components/documents/DocumentEditor";
import {
    createQuote,
    getQuote,
    updateQuote,
    type QuoteCreatePayload,
    type QuoteResponse,
} from "@/services/quoteApi";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface UseQuoteFormReturn {
    initialData: QuoteResponse | null;
    isLoading: boolean;
    isFetching: boolean;
    error: string | null;
    isRestricted: boolean;
    handleSave: (payload: DocumentPayload) => Promise<void>;
    handleCancel: () => void;
}

export function useQuoteForm(quoteId?: string): UseQuoteFormReturn {
    const [isFetching, setIsFetching] = useState(!!quoteId);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [initialData, setInitialData] = useState<QuoteResponse | null>(null);
    const navigate = useNavigate();

    const fetchQuote = useCallback(async () => {
        if (!quoteId) return;

        try {
            setIsFetching(true);
            const data = await getQuote(quoteId);

            if (!data.is_editable) {
                navigate(`/quotes/${quoteId}`, { replace: true });
                return;
            }

            setInitialData(data);
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to load quote"
            );
        } finally {
            setIsFetching(false);
        }
    }, [quoteId, navigate]);

    useEffect(() => {
        if (quoteId) void (async () => { await fetchQuote(); })();
    }, [quoteId, fetchQuote]);

    const handleCancel = () => {
        navigate(quoteId ? `/quotes/${quoteId}` : "/quotes");
    };

    const handleSave = async (payload: DocumentPayload) => {
        try {
            setIsLoading(true);
            setError(null);

            const quotePayload: QuoteCreatePayload = {
                customerId: payload.customerId,
                transactionDate: payload.transactionDate,
                dueDate: payload.dueDate,
                currency: payload.currency,
                lineItems: payload.lineItems.map((li) => ({
                    itemName: li.itemName ?? "",
                    description: li.description,
                    quantity: li.quantity,
                    unitPrice: li.unitPrice,
                    taxType: li.taxType,
                })),
                rfqRfpNumber: payload.rfqNumber,
                notes: payload.notes,
                discountType: payload.discountType,
                discountAmount: payload.discountAmount,
                discountPercentage: payload.discountPercentage,
            };

            if (quoteId && initialData) {
                await updateQuote(quoteId, quotePayload, initialData.version);
                navigate(`/quotes/${quoteId}`);
            } else {
                const quote = await createQuote(quotePayload);
                navigate(`/quotes/${quote.id}`);
            }
        } catch (err) {
            setError(
                err instanceof Error
                    ? err.message
                    : quoteId
                        ? "Failed to update quote"
                        : "Failed to create quote"
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