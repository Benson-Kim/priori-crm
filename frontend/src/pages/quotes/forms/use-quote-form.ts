import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { getQuote, createQuote, updateQuote, type QuoteResponse, type QuoteCreatePayload } from "@/lib/quoteApi";

export function useQuoteForm(quoteId?: string) {
    const navigate = useNavigate();
    const [initialData, setInitialData] = useState<QuoteResponse | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState("");

    const fetchQuote = useCallback(async () => {
        if (!quoteId) return;
        setIsLoading(true);
        setError("");
        try {
            const data = await getQuote(quoteId);
            setInitialData(data);
        } catch (err: unknown) {
            console.error("[useQuoteForm] Failed to fetch quote:", err);
            setError(err instanceof Error ? err.message : "Failed to load quote details. It may not exist or you don't have permission.");
        } finally {
            setIsLoading(false);
        }
    }, [quoteId]);

    useEffect(() => {
        fetchQuote();
    }, [fetchQuote]);

    const handleSave = async (payload: QuoteCreatePayload) => {
        setIsSaving(true);
        setError("");
        try {
            if (quoteId && initialData) {
                await updateQuote(quoteId, payload, initialData.version);
                console.log(`[useQuoteForm] Quote updated successfully: ${quoteId}`);
                navigate(`/quotes/${quoteId}`); // Go to preview
            } else {
                const res = await createQuote(payload);
                console.log(`[useQuoteForm] Quote created successfully: ${res.id}`);
                navigate(`/quotes/${res.id}`); // Go to preview
            }
        } catch (err: unknown) {
            console.error("[useQuoteForm] Save failed:", err);
            setError(err instanceof Error ? err.message : "Failed to save quote. Please try again.");
            throw err;
        } finally {
            setIsSaving(false);
        }
    };

    const handleCancel = () => {
        if (quoteId) {
            navigate(`/quotes/${quoteId}`);
        } else {
            navigate("/quotes");
        }
    };

    // Derived states
    const isRestricted = !!initialData && !initialData.is_editable;

    return {
        initialData,
        isLoading,
        isSaving,
        error,
        handleSave,
        handleCancel,
        isRestricted,
    };
}
