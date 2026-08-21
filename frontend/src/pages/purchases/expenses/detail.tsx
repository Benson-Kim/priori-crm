import { ExpenseViewer } from "@/components/documents/ExpenseViewer";
import { useHeaderOverride } from "@/components/layout/header-context";
import { RecordPaymentModal } from "@/components/modals/RecordPaymentModal";
import { Button } from "@/components/ui/Button";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { useConfirm } from "@/hooks/useConfirm";
import { ACCEPTED_UPLOAD_TYPES } from "@/lib/constants";
import { saveBlob } from "@/lib/utils";
import type { ExpenseLineItem, ExpenseResponse } from "@/services/expenseApi";
import { deleteExpense, deleteExpenseDocument, downloadExpenseDocument, getExpense, uploadExpenseDocument } from "@/services/expenseApi";
import { ArrowLeft, Ban, CreditCard, Download, PaperclipIcon, Pencil, Plus, X } from "lucide-react";
import { startTransition, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

export default function ExpensesDetailPage() {
    const { id } = useParams<{ id: string }>();
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();

    const [expense, setExpense] = useState<ExpenseResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [showPaymentModal, setShowPaymentModal] = useState(false);
    const { showConfirm, ConfirmDialog } = useConfirm();

    // Document upload state
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);

    useHeaderOverride(expense?.expense_reference, "");

    const fetchExpense = useCallback(async () => {
        if (!id) return;
        setIsLoading(true);
        setError(null);
        try {
            const data = await getExpense(id);
            setExpense(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load expense");
        } finally {
            setIsLoading(false);
        }
    }, [id]);

    useEffect(() => {
        void (async () => { await fetchExpense(); })();
    }, [fetchExpense]);

    useEffect(() => {
        if (searchParams.get("action") === "record-payment" && expense) {
            startTransition(() => { setShowPaymentModal(true); });
        }
    }, [searchParams, expense]);

    const handleDelete = () => {
        if (!expense) return;
        showConfirm({
            title: "Delete Expense",
            description: `Delete expense ${expense.expense_reference}? This action is irreversible.`,
            confirmLabel: "Delete",
            variant: "danger",
            onConfirm: async () => {
                await deleteExpense(expense.id);
                navigate("/expenses");
            },
        });
    };

    const handlePaymentClose = () => {
        setShowPaymentModal(false);
        if (id) navigate(`/expenses/${id}`, { replace: true });
    };

    const handlePaymentSuccess = () => {
        setShowPaymentModal(false);
        if (id) navigate(`/expenses/${id}`, { replace: true });
        fetchExpense();
    };

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !id) return;

        try {
            setIsUploading(true);
            await uploadExpenseDocument(id, file);
            fetchExpense(); // Refresh to get the new document
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to upload document");
        } finally {
            setIsUploading(false);
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        }
    };

    const handleFileDelete = (docId: string, filename: string) => {
        if (!id) return;
        showConfirm({
            title: "Delete Document",
            description: `Are you sure you want to delete ${filename}?`,
            confirmLabel: "Delete",
            variant: "danger",
            onConfirm: async () => {
                await deleteExpenseDocument(id, docId);
                fetchExpense();
            },
        });
    };

    const handleFileDownload = (docId: string, filename: string) => {
        if (!id) return;
        showConfirm({
            title: "Download Document",
            description: `Are you sure you want to download ${filename}?`,
            confirmLabel: "Download",
            variant: "default",
            onConfirm: async () => {
                try {
                    const blob = await downloadExpenseDocument(id, docId);
                    saveBlob(blob, filename);
                } catch (err) {
                    setError(
                        err instanceof Error
                            ? err.message
                            : "Failed to download document"
                    );
                }
            },
        });
    };

    if (isLoading) {
        return <LoadingState message="Loading expense..." />;
    }

    if (error || !expense) {
        return (
            <div className="flex flex-col items-center justify-center h-40 gap-4">
                <p className="text-red-500">{error ?? "Expense not found"}</p>
                <Button variant="outline" onClick={() => navigate("/expenses")}>
                    <ArrowLeft size={16} /> Back to Expenses
                </Button>
            </div>
        );
    }

    const status = expense.status.toLowerCase();
    const actions: DropdownItem[] = [];

    if (expense.is_editable) {
        actions.push({
            key: "edit",
            label: "Edit",
            icon: <Pencil size={16} />,
            onClick: () => navigate(`/expenses/${expense.id}/edit`),
        });
    }

    if (["pending", "overdue"].includes(status)) {
        actions.push({
            key: "payment",
            label: "Record Payment",
            icon: <CreditCard size={16} />,
            onClick: () => setShowPaymentModal(true),
        });
    }

    actions.push({
        key: "delete",
        label: "Delete Expense",
        icon: <Ban size={16} />,
        onClick: handleDelete,
        danger: true,
    });

    return (
        <div className="flex flex-col gap-6 font-sans pb-10">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => navigate("/expenses")}
                        aria-label="Back to expenses"
                    >
                        <ArrowLeft size={20} />
                    </Button>
                </div>

                <Dropdown
                    items={actions}
                    className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
                />
            </div>

            {/* Error banner */}
            {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                    {error}
                </div>
            )}

            {/* Document Viewer */}
            <ExpenseViewer
                data={{
                    expenseReference: expense.expense_reference,
                    vendorId: expense.vendor_id,
                    vendor: expense.vendor,
                    expenseDate: expense.expense_date,
                    dueDate: expense.due_date,
                    currency: expense.currency,
                    isRecurring: expense.is_recurring,
                    notes: expense.notes || undefined,
                    subtotal: Number(expense.subtotal),
                    taxTotal: Number(expense.tax_total),
                    totalDue: Number(expense.total_due),
                    amountPaid: Number(expense.amount_paid),
                    balanceDue: Number(expense.balance_due),
                    lineItems: (expense.line_items ?? []).map((item: ExpenseLineItem, index) => ({
                        id: item.id ?? `line-${index}`,
                        itemName: item.item_name,
                        description: item.description,
                        quantity: Number(item.quantity),
                        unitPrice: Number(item.unit_price),
                        taxType: item.tax_type,
                        lineTotal: Number(item.line_total ?? Number(item.quantity) * Number(item.unit_price)),
                    })),
                }}
            />

            {/* Documents Section */}
            <div className="flex flex-col gap-2.5">
                <div className="flex items-center justify-between py-4">
                    <h3 className="text-xl font-bold text-gray-800">Documents</h3>
                    <Button
                        variant="outline"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={isUploading}
                    >
                        <Plus size={16} /> {isUploading ? "Uploading..." : "Attach Document"}
                    </Button>
                    <input
                        type="file"
                        ref={fileInputRef}
                        className="hidden"
                        onChange={handleFileUpload}
                        accept={ACCEPTED_UPLOAD_TYPES}
                    />
                </div>

                <div className="flex flex-col w-full sm:flex sm:items-center sm:justify-between gap-4">
                    {expense.documents?.length === 0 && !isUploading && (
                        <div className="col-span-full w-full py-4 text-center text-gray-400 border-2 border-dashed border-gray-200 rounded-xl">
                            No documents attached yet.
                        </div>
                    )}
                    {expense.documents?.map((doc) => (
                        <div key={doc.id} className="flex items-center justify-between w-full py-4 transition-colors">
                            <div className="flex items-center gap-3 overflow-hidden">
                                <PaperclipIcon size={24} className="text-gray-700" />
                                <div className="min-w-0">
                                    <p className="text-gray-800 text-base truncate" title={doc.filename}>{doc.filename}</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-6">
                                <p className="text-base text-gray-500">{doc.file_size_kb.toFixed(1)} KB</p>
                                <Button onClick={() => handleFileDownload(doc.id, doc.filename)} aria-label="Download document"
                                    className="p-0 border-0 shadow-none bg-transparent flex items-center gap-2 text-priori-purple hover:text-priori-purple hover:bg-transparent">
                                    <Download size={24} /> <span className="text-base text-priori-purple">Download</span>
                                </Button>
                                <Button onClick={() => handleFileDelete(doc.id, doc.filename)} aria-label="Delete document"
                                    className="p-0 border-0 shadow-none bg-transparent flex items-center gap-2 text-gray-600 hover:text-priori-purple hover:bg-transparent">
                                    <X size={24} /> <span className="text-base text-gray-800">Delete</span>
                                </Button>
                            </div>
                        </div>
                    ))}
                    {isUploading && (
                        <div className="flex items-center justify-center p-4 border border-gray-200 rounded-xl bg-gray-50 opacity-50">
                            <div className="flex items-center gap-2 text-priori-purple">
                                <div className="w-4 h-4 border-2 border-priori-purple border-t-transparent rounded-full animate-spin"></div>
                                <span className="text-sm font-medium">Uploading...</span>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Record Payment Modal */}
            <RecordPaymentModal
                isOpen={showPaymentModal}
                onClose={handlePaymentClose}
                entityId={expense.id}
                entityType="expense"
                balanceDue={Number(expense.balance_due)}
                currency={expense.currency}
                reference={expense.expense_reference}
                onSuccess={handlePaymentSuccess}
            />

            {/* Confirm Dialog */}
            {ConfirmDialog}
        </div>
    );
}
