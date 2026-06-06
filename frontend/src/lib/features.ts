/**
 * Single feature/contract source (P-10).
 *
 * One declarative map of which document actions are backed by a live API
 * endpoint, so the frontend and backend can no longer disagree implicitly
 * through scattered `alert("coming soon")` stubs or dead buttons. UI controls
 * should read this map (or be removed) rather than hardcoding availability.
 *
 * Keep this in sync with the routers; once OpenAPI codegen (LIB-FE-9) is part
 * of CI, a follow-up can assert these against the generated `paths`.
 */
export const DOCUMENT_FEATURES = {
  invoice: {
    downloadPdf: true, // GET /invoices/{id}/pdf
    send: true, //        POST /invoices/{id}/send
    exportExcel: true, //  GET /invoices/export/excel
  },
  quote: {
    downloadPdf: true, // GET /quotes/{id}/pdf
    send: true, //        POST /quotes/{id}/send
    exportExcel: true, //  GET /quotes/export/excel
  },
  expense: {
    downloadPdf: false, // no /expenses/{id}/pdf endpoint exists
    documentDownload: true, // GET /expenses/{id}/documents/{doc}/download
    exportExcel: true, //  GET /expenses/export/excel
  },
  vendor: {
    exportExcel: false, // no vendor Excel endpoint exists
  },
} as const;

export type DocumentKind = keyof typeof DOCUMENT_FEATURES;
