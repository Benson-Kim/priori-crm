/**
 * PurchaseOrderPaymentModal — view a single recorded payment and attach
 * related proof-of-payment document(s) to it.
 *
 * Payments are immutable financial records, so the amount/date/reference are
 * read-only here. The supported "update" is attaching additional documents
 * (uploaded with source `payment_modal` and grouped under the payment via
 * paymentId). A payment can carry any number of proof documents; they are
 * resolved from the PO's document set by `payment_id` (with the legacy
 * single `payment.document_id` included for back-compat) and all are listed.
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
import { CreditCard, Download, Eye, Paperclip, Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Input } from "../ui/Input";
import { DocumentPreviewModal } from "./DocumentPreviewModal";

interface PurchaseOrderPaymentModalProps {
    isOpen: boolean;
    onClose: () => void;
    poId: string;
    payment: PurchaseOrderPayment | null;
    /** All PO documents, used to resolve this payment's related attachments. */
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
    // Document selected for inline preview (one of this payment's documents).
    const [previewDocument, setPreviewDocument] = useState<PurchaseOrderDocument | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (isOpen) {
            setFiles([]);
            setError(null);
            setPreviewDocument(null);
        }
    }, [isOpen, payment?.id]);

    if (!payment) return null;

    // All proof-of-payment documents related to this payment: those grouped
    // under it via payment_id, plus the legacy single document_id link (kept
    // for back-compat). De-duplicated by id.
    const relatedDocuments = documents.filter(
        (d) => d.payment_id === payment.id || d.id === payment.document_id
    );

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
            // Group every attached file under this payment so they all appear
            // on the payment (resolved on view via payment_id).
            for (const file of files) {
                await uploadPurchaseOrderDocument(poId, file, "payment_modal", payment.id);
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
            <div className="space-y-6 bg-white p-6 rounded-xl border border-gray-200">
                {error && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
                )}

                <div className="space-y-2">
                    <Label htmlFor="payment-amount" className="font-bold text-sm">Amount</Label>
                    <Input
                        className="text-gray-700"
                        id="payment-amount"
                        type="text"
                        value={formatCurrency(Number(payment.amount), currency)}
                        disabled
                    />
                </div>
                <div className="space-y-2">
                    <Label htmlFor="payment-date" className="font-bold text-sm">Date</Label>
                    <Input
                        className="text-gray-700"
                        id="payment-date"
                        type="text"
                        value={payment.payment_date ? formatDisplayDate(payment.payment_date) : "-"}
                        disabled
                    />
                </div>
                {payment.reference && (
                    <div className="space-y-2">
                        <Label htmlFor="payment-reference" className="font-bold text-sm">Reference</Label>
                        <Input
                            className="text-gray-700"
                            id="payment-reference"
                            type="text"
                            value={payment.reference}
                            disabled
                        />
                    </div>
                )}
                {payment.notes && (
                    <div className="space-y-2">
                        <Label htmlFor="payment-notes" className="font-bold text-sm">Notes</Label>
                        <Input
                            className="text-gray-700"
                            id="payment-notes"
                            type="text"
                            value={payment.notes}
                            disabled
                        />
                    </div>
                )}

                {/* Related proof-of-payment documents (all of them). */}
                <div className="space-y-2">
                    <Label>Related documents</Label>
                    {relatedDocuments.length > 0 ? (
                        <ul className="flex flex-col gap-2">
                            {relatedDocuments.map((doc) => (
                                <li
                                    key={doc.id}
                                    className="flex items-center justify-between gap-3 px-3 py-4 bg-gray-50 border border-gray-200 rounded-lg"
                                >
                                    <span className="flex items-center gap-2 min-w-0 text-sm text-gray-700">
                                        <Paperclip size={16} className="shrink-0 text-gray-500" />
                                        <span className="truncate" title={doc.filename}>
                                            {doc.filename}
                                        </span>
                                    </span>
                                    <div className="flex items-center gap-4 shrink-0">
                                        <button
                                            type="button"
                                            onClick={() => setPreviewDocument(doc)}
                                            className="flex items-center gap-2 text-priori-purple hover:underline text-sm"
                                            aria-label={`Preview ${doc.filename}`}
                                        >
                                            <Eye size={18} /> Preview
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => handleDownload(doc.id, doc.filename)}
                                            className="flex items-center gap-2 text-priori-purple hover:underline text-sm"
                                            aria-label={`Download ${doc.filename}`}
                                        >
                                            <Download size={18} /> Download
                                        </button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className="text-sm text-gray-400">No documents linked to this payment.</p>
                    )}
                </div>

                {/* Attach additional document(s). */}
                <div className="space-y-2">
                    <Button
                        type="button"
                        variant="outline"
                        onClick={() => fileInputRef.current?.click()}
                        className="w-full border-dashed h-28 "
                    >
                        <Plus size={20} /> Upload document
                    </Button>
                    <input
                        id="payment-documents"
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
                                    className="flex items-center justify-between gap-3 px-3 py-4 bg-gray-50 border border-gray-200 rounded-lg transition-all"
                                >
                                    <span className="flex items-center gap-2 min-w-0 text-lg text-gray-700">
                                        <Paperclip size={20} className="shrink-0 text-gray-500" />
                                        <span className="truncate" title={file.name}>{file.name}</span>
                                    </span>
                                    <button
                                        type="button"
                                        onClick={() => removeFile(index)}
                                        className="text-gray-400 hover:text-red-500 transition-colors"
                                        aria-label={`Remove ${file.name}`}
                                    >
                                        <X size={20} />
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>

            {/* Inline preview of a selected proof-of-payment document. */}
            {previewDocument && (
                <DocumentPreviewModal
                    isOpen={previewDocument !== null}
                    onClose={() => setPreviewDocument(null)}
                    poId={poId}
                    documentId={previewDocument.id}
                    filename={previewDocument.filename}
                    mimeType={previewDocument.mime_type}
                />
            )}
        </Dialog>
    );
}
