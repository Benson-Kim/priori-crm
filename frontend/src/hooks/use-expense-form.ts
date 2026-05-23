import type { ExpensePayload } from "@/components/documents/ExpenseEditor";
import { createExpense, getExpense, updateExpense, type ExpenseCreatePayload, type ExpenseResponse, type ExpenseUpdatePayload } from "@/services/expenseApi";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface UseExpenseFormReturn {
    initialData: ExpenseResponse | null;
    isLoading: boolean;
    isFetching: boolean;
    error: string | null;
    isRestricted: boolean;
    handleSave: (payload: ExpensePayload) => Promise<void>;
    handleCancel: () => void;
}

export function useExpenseForm(expenseId?: string): UseExpenseFormReturn {
    const [isFetching, setIsFetching] = useState(!!expenseId);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [initialData, setInitialData] = useState<ExpenseResponse | null>(null);
    const navigate = useNavigate();

    const fetchExpense = useCallback(async () => {
        if (!expenseId) return;

        try {
            setIsFetching(true);
            const data = await getExpense(expenseId);

            // Check if editable
            if (!data.is_editable) {
                navigate(`/expenses/${expenseId}`, { replace: true });
                return;
            }

            setInitialData(data);
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to load expense"
            );
        } finally {
            setIsFetching(false);
        }
    }, [expenseId, navigate]);

    useEffect(() => {
        if (expenseId) fetchExpense();
    }, [expenseId, fetchExpense]);

    const handleCancel = () => {
        navigate(expenseId ? `/expenses/${expenseId}` : "/expenses");
    };

    const handleSave = async (payload: ExpensePayload) => {
        try {
            setIsLoading(true);
            setError(null);

            const expensePayload: ExpenseCreatePayload = {
                vendorId: payload.vendorId,
                expenseDate: payload.expenseDate,
                dueDate: payload.dueDate,
                currency: payload.currency,
                isRecurring: payload.isRecurring,
                lineItems: payload.lineItems.map((li) => ({
                    item_name: li.itemName,
                    description: li.description,
                    quantity: li.quantity,
                    unit_price: li.unitPrice,
                    tax_type: li.taxType,
                })),
                notes: payload.notes,
            };

            let expenseIdToNavigate = expenseId;

            if (expenseId && initialData) {
                const updatePayload: ExpenseUpdatePayload = {
                    vendorId: payload.vendorId,
                    expenseDate: payload.expenseDate,
                    dueDate: payload.dueDate,
                    isRecurring: payload.isRecurring,
                    lineItems: payload.lineItems.map((li) => ({
                        item_name: li.itemName,
                        description: li.description,
                        quantity: li.quantity,
                        unit_price: li.unitPrice,
                        tax_type: li.taxType,
                    })),
                    notes: payload.notes,
                };
                await updateExpense(expenseId, updatePayload);
            } else {
                const expense = await createExpense(expensePayload);
                expenseIdToNavigate = expense.id;
            }

            // Upload any queued files
            if (payload.files && payload.files.length > 0 && expenseIdToNavigate) {
                // Ensure the upload happens after saving
                const { uploadExpenseDocument } = await import("@/services/expenseApi");
                await Promise.all(
                    payload.files.map(file => uploadExpenseDocument(expenseIdToNavigate!, file))
                );
            }

            navigate(`/expenses/${expenseIdToNavigate}`);
        } catch (err) {
            setError(
                err instanceof Error
                    ? err.message
                    : expenseId
                        ? "Failed to update expense"
                        : "Failed to create expense"
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
        isRestricted: !initialData?.is_editable,
        handleSave,
        handleCancel,
    };
}
