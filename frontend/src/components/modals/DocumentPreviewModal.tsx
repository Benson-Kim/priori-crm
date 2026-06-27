/**
 * DocumentPreviewModal — Gmail/Drive-style inline preview of a purchase-order document.
 *
 * The bytes are fetched through the authed download client into an in-memory
 * object URL, then rendered inline:
 *   - PDFs are rendered page-by-page with react-pdf / pdf.js,
 *   - images are rendered in an <img>,
 *   - other types fall back to a Download button.
 *
 * This avoids the browser's native PDF iframe viewer, so no unwanted PDF
 * toolbar appears inside the preview.
 */
import { Button } from "@/components/ui/Button";
import { saveBlob } from "@/lib/utils";
import { downloadPurchaseOrderDocument } from "@/services/purchaseOrderApi";
import { Download, ExternalLink, FileText, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Document, Page, pdfjs } from "react-pdf";

import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Bundle the pdf.js worker locally so its version always matches react-pdf's
// pinned pdfjs-dist. A CDN worker whose version drifts (or whose .mjs path
// 404s) leaves the worker uninitialized and pdf.js reports the document as
// empty/invalid.
pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

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
    const [pdfData, setPdfData] = useState<ArrayBuffer | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [numPages, setNumPages] = useState(0);
    const [previewWidth, setPreviewWidth] = useState(900);

    const previewContainerRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (!isOpen || !documentId) return;

        let cancelled = false;
        let createdUrl: string | null = null;

        setIsLoading(true);
        setError(null);
        setBlob(null);
        setObjectUrl(null);
        setPdfData(null)
        setNumPages(0);

        (async () => {
            try {
                const fetched = await downloadPurchaseOrderDocument(poId, documentId);
                if (cancelled) return;

                const buffer = await fetched.arrayBuffer();
                if (cancelled) return;

                createdUrl = URL.createObjectURL(fetched);
                setBlob(fetched);
                setPdfData(buffer);
                setObjectUrl(createdUrl);
            } catch (err) {
                if (!cancelled) {
                    setError(
                        err instanceof Error ? err.message : "Failed to load document"
                    );
                }
            } finally {
                if (!cancelled) {
                    setIsLoading(false);
                }
            }
        })();

        return () => {
            cancelled = true;

            if (createdUrl) {
                URL.revokeObjectURL(createdUrl);
            }

            setBlob(null);
            setObjectUrl(null);
            setPdfData(null);
            setNumPages(0);
        };
    }, [isOpen, poId, documentId]);

    useEffect(() => {
        if (!isOpen) return;

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                onClose();
            }
        };

        window.addEventListener("keydown", handleKeyDown);

        return () => {
            window.removeEventListener("keydown", handleKeyDown);
        };
    }, [isOpen, onClose]);

    useEffect(() => {
        if (!isOpen) return;

        const originalOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";

        return () => {
            document.body.style.overflow = originalOverflow;
        };
    }, [isOpen]);

    useEffect(() => {
        if (!isOpen) return;

        const container = previewContainerRef.current;

        if (!container) return;

        const updateWidth = () => {
            const availableWidth = container.clientWidth;

            /**
             * Gmail/Drive-like behavior:
             * - fit comfortably on smaller screens,
             * - cap the page width on desktop,
             * - avoid becoming too narrow.
             */
            const nextWidth = Math.min(
                Math.max(availableWidth - 32, 320),
                900
            );

            setPreviewWidth(nextWidth);
        };

        updateWidth();

        const resizeObserver = new ResizeObserver(updateWidth);
        resizeObserver.observe(container);

        return () => {
            resizeObserver.disconnect();
        };
    }, [isOpen]);

    const resolvedType = mimeType || blob?.type || "";

    const isPdf =
        resolvedType === "application/pdf" ||
        filename.toLowerCase().endsWith(".pdf");

    const isImage = resolvedType.startsWith("image/");

    const pageNumbers = useMemo(() => {
        return Array.from({ length: numPages }, (_, index) => index + 1);
    }, [numPages]);

    const pdfFile = useMemo(
        () => (pdfData ? { data: pdfData.slice(0) } : null),
        [pdfData]
    );

    const handleDownload = () => {
        if (blob) {
            saveBlob(blob, filename);
        }
    };

    const handleOpenInNewTab = () => {
        if (objectUrl) {
            window.open(objectUrl, "_blank", "noopener,noreferrer");
        }
    };

    if (!isOpen || !documentId) return null;

    return createPortal(
        <div
            className="fixed inset-0 z-50 bg-white/70 backdrop-blur-sm"
            role="dialog"
            aria-modal="true"
            aria-label={filename}
        >
            {/* Backdrop click target */}
            <button
                type="button"
                aria-label="Close preview backdrop"
                onClick={onClose}
                className="absolute inset-0 cursor-default"
            />

            {/* File name pill */}
            <div className="fixed left-5 top-5 z-20 flex max-w-[calc(100vw-11rem)] items-center gap-3 rounded-full bg-white px-5 py-3 text-gray-800 shadow-lg ring-1 ring-black/10">
                <FileText size={19} className="shrink-0 text-gray-500" />

                <span className="truncate text-sm font-medium" title={filename}>
                    {filename}
                </span>
            </div>

            {/* Action buttons */}
            <div className="fixed right-5 top-5 z-20 flex items-center gap-1 rounded-full bg-white p-1.5 shadow-lg ring-1 ring-black/10">
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={handleDownload}
                    disabled={!blob}
                    aria-label={`Download ${filename}`}
                    title={`Download ${filename}`}
                    className="rounded-full text-gray-700 hover:bg-gray-100 hover:text-gray-950"
                >
                    <Download size={20} />
                </Button>

                <Button
                    variant="ghost"
                    size="icon"
                    onClick={handleOpenInNewTab}
                    disabled={!objectUrl}
                    aria-label="Open in new tab"
                    title="Open in new tab"
                    className="rounded-full text-gray-700 hover:bg-gray-100 hover:text-gray-950"
                >
                    <ExternalLink size={20} />
                </Button>

                <Button
                    variant="ghost"
                    size="icon"
                    onClick={onClose}
                    aria-label="Close preview"
                    title="Close preview"
                    className="rounded-full text-gray-700 hover:bg-gray-100 hover:text-gray-950"
                >
                    <X size={22} />
                </Button>
            </div>

            {/* Scrollable document area */}
            <div className="relative z-10 h-screen overflow-y-auto px-4 pb-12 pt-24">
                <div
                    ref={previewContainerRef}
                    className="mx-auto flex w-full max-w-5xl justify-center"
                >
                    {isLoading && (
                        <div className="mt-20 flex items-center gap-2 rounded-full bg-white px-5 py-3 text-gray-700 shadow-lg ring-1 ring-black/10">
                            <Loader2 size={20} className="animate-spin" />
                            Loading preview...
                        </div>
                    )}

                    {!isLoading && error && (
                        <div className="mt-20 rounded-xl bg-white p-6 text-center shadow-xl ring-1 ring-black/10">
                            <p className="mb-4 text-error-600">{error}</p>

                            <Button variant="outline" onClick={onClose}>
                                Close
                            </Button>
                        </div>
                    )}

                    {!isLoading && !error && pdfFile && isPdf && (
                        <Document
                            file={pdfFile}
                            loading={
                                <div className="mt-20 flex items-center gap-2 rounded-full bg-white px-5 py-3 text-gray-700 shadow-lg ring-1 ring-black/10">
                                    <Loader2 size={20} className="animate-spin" />
                                    Rendering document...
                                </div>
                            }
                            error={
                                <div className="mt-20 rounded-xl bg-white p-6 text-center shadow-xl ring-1 ring-black/10">
                                    <p className="mb-4 text-error-600">
                                        Failed to render PDF preview.
                                    </p>

                                    <Button variant="outline" onClick={handleDownload}>
                                        Download instead
                                    </Button>
                                </div>
                            }
                            onLoadSuccess={({ numPages }) => {
                                setNumPages(numPages);
                            }}
                            className="flex w-full flex-col items-center gap-6"
                        >
                            {pageNumbers.map((pageNumber) => (
                                <div
                                    key={pageNumber}
                                    className="overflow-hidden bg-white shadow-2xl ring-1 ring-black/10"
                                >
                                    <Page
                                        pageNumber={pageNumber}
                                        width={previewWidth}
                                        renderAnnotationLayer={false}
                                        renderTextLayer={false}
                                    />
                                </div>
                            ))}
                        </Document>
                    )}

                    {!isLoading && !error && objectUrl && isImage && (
                        <div className="bg-white p-3 shadow-2xl ring-1 ring-black/10">
                            <img
                                src={objectUrl}
                                alt={filename}
                                className="max-w-full object-contain"
                            />
                        </div>
                    )}

                    {!isLoading && !error && objectUrl && !isPdf && !isImage && (
                        <div className="mt-20 flex flex-col items-center gap-4 rounded-xl bg-white p-8 text-center shadow-2xl ring-1 ring-black/10">
                            <FileText size={48} className="text-gray-400" />

                            <p className="text-gray-600">
                                This file type can&apos;t be previewed inline.
                            </p>

                            <Button variant="primary" onClick={handleDownload}>
                                <Download size={18} />
                                Download {filename}
                            </Button>
                        </div>
                    )}
                </div>
            </div>
        </div>,
        document.body
    );
}