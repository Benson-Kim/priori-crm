/**
 * DocumentPreviewModal — inline preview of a purchase-order document.
 *
 * The bytes are fetched through the authed download client (so the request
 * carries the bearer token and refreshes on 401) into an in-memory object
 * URL, then rendered inline:
 *   - PDFs in an <iframe>,
 *   - images in an <img>,
 *   - any other type falls back to a Download button.
 *
 * The object URL is created when the modal opens and revoked when it closes
 * or the document changes, so blobs are never leaked.
 */
import { Button } from "@/components/ui/Button";
import { saveBlob } from "@/lib/utils";
import { downloadPurchaseOrderDocument } from "@/services/purchaseOrderApi";
import { Download, FileText, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

interface DocumentPreviewModalProps {
    isOpen: boolean;
    onClose: () => void;
    poId: string;
    documentId: string | null;
    filename: string;
    /** MIME type from the document record; used to choose the viewer. */
    mimeType?: string;
}

export function DocumentPreviewModal({
    isOpen,
    onClose,
    poId,
    documentId,
    filename,
    mimeType,
}: Readonly<DocumentPreviewModalProps>) {
    const [objectUrl, setObjectUrl] = useState<string | null>(null);
    const [blob, setBlob] = useState<Blob | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!isOpen || !documentId) return;

        let revoked = false;
        let createdUrl: string | null = null;

        setIsLoading(true);
        setError(null);

        (async () => {
            try {
                const fetched = await downloadPurchaseOrderDocument(poId, documentId);
                if (revoked) return;
                createdUrl = URL.createObjectURL(fetched);
                setBlob(fetched);
                setObjectUrl(createdUrl);
            } catch (err) {
                if (!revoked) {
                    setError(
                        err instanceof Error ? err.message : "Failed to load document"
                    );
                }
            } finally {
                if (!revoked) setIsLoading(false);
            }
        })();

        return () => {
            revoked = true;
            if (createdUrl) URL.revokeObjectURL(createdUrl);
            setObjectUrl(null);
            setBlob(null);
        };
    }, [isOpen, poId, documentId]);

    if (!isOpen || !documentId) return null;

    // Prefer the record's declared MIME type, falling back to the blob's.
    const resolvedType = mimeType || blob?.type || "";
    const isPdf = resolvedType === "application/pdf" || filename.toLowerCase().endsWith(".pdf");
    const isImage = resolvedType.startsWith("image/");

    const handleDownload = () => {
        if (blob) saveBlob(blob, filename);
    };

    return createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div
                className="absolute inset-0 bg-black/50 backdrop-blur-sm"
                onClick={onClose}
            />
            <div className="relative w-full max-w-4xl mx-4 rounded-2xl bg-white shadow-xl overflow-hidden max-h-[92vh] flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
                    <div className="flex items-center gap-2 min-w-0 text-gray-800">
                        <FileText size={22} className="shrink-0 text-priori-purple" />
                        <h3 className="text-lg font-bold truncate" title={filename}>
                            {filename}
                        </h3>
                    </div>
                    <div className="flex items-center gap-3">
                        <Button
                            variant="outline-secondary"
                            onClick={handleDownload}
                            disabled={!blob}
                            className="flex items-center gap-2"
                        >
                            <Download size={18} /> Download
                        </Button>
                        <button
                            onClick={onClose}
                            className="text-gray-500 hover:text-gray-700 transition-colors"
                            aria-label="Close preview"
                        >
                            <X size={28} />
                        </button>
                    </div>
                </div>

                {/* Body */}
                <div className="flex-1 min-h-[60vh] bg-gray-50 flex items-center justify-center overflow-auto">
                    {isLoading && (
                        <div className="flex items-center gap-2 text-priori-purple">
                            <Loader2 size={20} className="animate-spin" /> Loading preview...
                        </div>
                    )}

                    {!isLoading && error && (
                        <div className="p-6 text-center">
                            <p className="text-red-600 mb-4">{error}</p>
                            <Button variant="outline" onClick={onClose}>Close</Button>
                        </div>
                    )}

                    {!isLoading && !error && objectUrl && isPdf && (
                        <iframe
                            src={objectUrl}
                            title={filename}
                            className="w-full h-[75vh] border-0"
                        />
                    )}

                    {!isLoading && !error && objectUrl && isImage && (
                        <img
                            src={objectUrl}
                            alt={filename}
                            className="max-w-full max-h-[75vh] object-contain"
                        />
                    )}

                    {!isLoading && !error && objectUrl && !isPdf && !isImage && (
                        <div className="p-8 text-center flex flex-col items-center gap-4">
                            <FileText size={48} className="text-gray-400" />
                            <p className="text-gray-600">
                                This file type can't be previewed inline.
                            </p>
                            <Button onClick={handleDownload} className="flex items-center gap-2">
                                <Download size={18} /> Download {filename}
                            </Button>
                        </div>
                    )}
                </div>
            </div>
        </div>,
        document.body
    );
}
