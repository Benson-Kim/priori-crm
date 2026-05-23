import { ExpenseEditor } from "@/components/documents/ExpenseEditor";
import { useExpenseForm } from "@/hooks/use-expense-form";

export default function AddExpensePage() {
    const { handleSave, isLoading, error } = useExpenseForm();

    return (
        <>

            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    {error}
                </div>
            )}
            <ExpenseEditor
                onSave={handleSave}
                isLoading={isLoading}
            />
        </>
    );
}
