import { ExpenseEditor } from "@/components/documents/ExpenseEditor";
import { LoadingState } from "@/components/ui/LoadingState";
import { useExpenseForm } from "@/hooks/use-expense-form";
import { useParams } from "react-router-dom";

export default function EditExpensePage() {
    const { id } = useParams<{ id: string }>();
    const { initialData, isFetching, isLoading, error, isRestricted, handleSave } =
        useExpenseForm(id);

    if (isFetching) {
        return <LoadingState message="Loading expense details..." />;
    }

    if (error || !initialData) {
        return (
            <div className="p-8 text-center text-red-500">
                {error ?? "Expense not found"}
            </div>
        );
    }

    return (
        <ExpenseEditor
            initialData={{
                expenseReference: initialData.expense_reference,
                vendorId: initialData.vendor_id,
                vendor: initialData.vendor,
                expenseDate: initialData.expense_date,
                dueDate: initialData.due_date,
                currency: initialData.currency,
                isRecurring: initialData.is_recurring,
                notes: initialData.notes || undefined,
                lineItems: initialData.line_items.map((li: any) => ({
                    id: li.id,
                    itemName: li.item_name,
                    description: li.description,
                    quantity: li.quantity,
                    unitPrice: li.unit_price,
                    taxType: li.tax_type,
                })),
            }}
            onSave={handleSave}
            isLoading={isLoading}
            restrictedMode={isRestricted}
        />
    );
}
