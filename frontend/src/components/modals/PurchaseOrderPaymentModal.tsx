/**
 * PurchaseOrderPaymentModal — view a single recorded payment and attach
 * related proof-of-payment document(s) to it.
 *
 * Payments are immutable financial records, so the amount/date/reference are
 * read-only here. The one supported "update" is attaching additional
 * documents (uploaded with source `payment_modal`) and linking the first one
 * to the payment when it has none yet.
 */
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Label } from "@/components/ui/Label";
import { ACCEPTED_UPLOAD_TYPES } from "@/lib/constants";
import { formatCurrency, formatDisplayDate, saveBlob } from "@/lib/utils";
import type {
    PurchaseOrderDocument,
    PurchaseOrderPayment,
} from "@/services/purchaseOrderApi";
import {
    downloadPurchaseOrderDocument,
    uploadPurchaseOrderDocument,
} from "@/services/purchaseOrderApi";
import { CreditCard, Download, Paperclip, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface PurchaseOrderPaymentModalProps {
    isOpen: boolean;
    onClose: () => void;
    poId: string;
    payment: PurchaseOrderPayment | null;
    /** All PO documents, used to resolve the payment's linked attachment. */
    documents: PurchaseOrderDocument[];
    currency: string;
    /** Called after a successful upload so the parent can refetch the PO. */
    onUpdated: () => void;
}

export function PurchaseOrderPaymentModal({
    isOpen,
    onClose,
    poId,
    payment,
    documents,
    currency,
    onUpdated,
}: Readonly<PurchaseOrderPaymentModalProps>) {
    const [files, setFiles] = useState<File[]>([]);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (isOpen) {
            setFiles([]);
            setError(null);
        }
    }, [isOpen, payment?.id]);

    if (!payment) return null;

    // The proof-of-payment document linked to this payment (if any).
    const linkedDocument = payment.document_id
        ? documents.find((d) => d.id === payment.document_id)
        : undefined;

    const handleFilesPicked = (e: React.ChangeEvent<HTMLInputElement>) => {
        const picked = Array.from(e.target.files ?? []);
        if (picked.length) setFiles((prev) => [...prev, ...picked]);
        if (fileInputRef.current) fileInputRef.current.value = "";
    };

    const removeFile = (index: number) =>
        setFiles((prev) => prev.filter((_, i) => i !== index));

    const handleDownload = async (docId: string, filename: string) => {
        try {
            const blob = await downloadPurchaseOrderDocument(poId, docId);
            saveBlob(blob, filename);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to download document");
        }
    };

    const handleSave = async () => {
        if (files.length === 0) {
            onClose();
            return;
        }
        setError(null);
        setIsSaving(true);
        try {
            for (const file of files) {
                await uploadPurchaseOrderDocument(poId, file, "payment_modal");
            }
            setFiles([]);
            onUpdated();
            onClose();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to attach document");
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Dialog
            isOpen={isOpen}
            onClose={onClose}
            title="Payment Details"
            icon={<CreditCard size={24} />}
            confirmLabel={files.length > 0 ? "Attach & Save" : "Done"}
            cancelLabel="Close"
            onConfirm={handleSave}
            isLoading={isSaving}
        >
            <div className="space-y-4">
                {error && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                        {error}
                    </div>
                )}

                {/* Read-only payment summary. */}
                <div className="grid grid-cols-2 gap-4 p-4 bg-white border border-gray-200 rounded-xl">
                    <div>
                        <p className="text-sm text-gray-500">Amount</p>
                        <p className="font-bold text-gray-800">
                            {formatCurrency(Number(payment.amount), currency)}
                        </p>
                    </div>
                    <div>
                        <p className="text-sm text-gray-500">Date</p>
                        <p className="font-bold text-gray-800">
                            {payment.payment_date ? formatDisplayDate(payment.payment_date) : "-"}
                        </p>
                    </div>
                    {payment.reference && (
                        <div>
                            <p className="text-sm text-gray-500">Reference</p>
                            <p className="font-medium text-gray-800 break-all">{payment.reference}</p>
                        </div>
                    )}
                    {payment.notes && (
                        <div className="col-span-2">
                            <p className="text-sm text-gray-500">Notes</p>
                            <p className="text-gray-700 whitespace-pre-wrap">{payment.notes}</p>
                        </div>
                    )}
                </div>

                {/* Linked proof-of-payment document. */}
                <div>
                    <Label>Related document</Label>
                    {linkedDocument ? (
                        <div className="flex items-center justify-between gap-3 px-3 py-2 bg-white border border-gray-200 rounded-lg">
                            <span className="flex items-center gap-2 min-w-0 text-sm text-gray-700">
                                <Paperclip size={16} className="shrink-0 text-gray-500" />
                                <span className="truncate" title={linkedDocument.filename}>
                                    {linkedDocument.filename}
                                </span>
                            </span>
                            <button
                                type="button"
                                onClick={() => handleDownload(linkedDocument.id, linkedDocument.filename)}
                                className="flex items-center gap-1 text-priori-purple hover:underline text-sm"
                            >
                                <Download size={16} /> Download
                            </button>
                        </div>
                    ) : (
                        <p className="text-sm text-gray-400">No document linked to this payment.</p>
                    )}
                </div>

                {/* Attach additional document(s). */}
                <div>
                    <Label htmlFor="po-payment-doc">Attach related document(s)</Label>
                    <Button
                        type="button"
                        variant="outline"
                        onClick={() => fileInputRef.current?.click()}
                        className="flex items-center gap-2"
                    >
                        <Paperclip size={16} /> Attach document
                    </Button>
                    <input
                        id="po-payment-doc"
                        ref={fileInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        accept={ACCEPTED_UPLOAD_TYPES}
                        onChange={handleFilesPicked}
                    />
                    {files.length > 0 && (
                        <ul className="mt-2 flex flex-col gap-2">
                            {files.map((file, index) => (
                                <li
                                    key={`${file.name}-${index}`}
                                    className="flex items-center justify-between gap-3 px-3 py-2 bg-white border border-gray-200 rounded-lg"
                                >
                                    <span className="flex items-center gap-2 min-w-0 text-sm text-gray-700">
                                        <Paperclip size={16} className="shrink-0 text-gray-500" />
                                        <span className="truncate" title={file.name}>{file.name}</span>
                                    </span>
                                    <button
                                        type="button"
                                        onClick={() => removeFile(index)}
                                        className="text-gray-400 hover:text-red-500 transition-colors"
                                        aria-label={`Remove ${file.name}`}
                                    >
                                        <X size={18} />
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>
        </Dialog>
    );
}
