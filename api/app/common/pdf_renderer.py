"""Shared PDF orchestration for customer-facing documents (ISSUE-009).

Invoices and quotes carried near-identical ``_render_pdf``/``generate_pdf``
implementations: resolve the owner header (the document's immutable snapshot
when issued, else the live profile), read the logo bytes, then call the
matching ``DocumentPDFGenerator`` method. ``DocumentPdfRenderer`` owns that
orchestration in one place; the services delegate to it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


class DocumentPdfRenderer:
    """Render invoice/quote PDFs with shared owner-branding resolution."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def load_owner_branding(self, entity: Any) -> tuple[Any, Any]:
        """Resolve the owner header (info + logo bytes) for a document PDF.

        Uses the document's immutable owner snapshot when issued, else the
        live profile, so editing the live profile can never re-brand an
        already-issued document.
        """
        from app.modules.owner.models import OwnerProfileSnapshot
        from app.modules.owner.service import OwnerService

        owner_service = OwnerService(self._db)
        if entity.owner_snapshot_id is not None:
            source = self._db.get(OwnerProfileSnapshot, entity.owner_snapshot_id)
        else:
            source = owner_service.get_or_create()
        return (
            owner_service.to_owner_info(source),
            owner_service.load_logo_bytes(source),
        )

    def render_invoice(self, invoice: Any) -> bytes:
        """Render a PDF for an already-loaded invoice."""
        from app.common.pdf import DocumentPDFGenerator

        owner_info, logo_bytes = self.load_owner_branding(invoice)
        return DocumentPDFGenerator().generate_invoice_pdf(
            invoice, owner=owner_info, logo_bytes=logo_bytes
        )

    def render_quote(self, quote: Any) -> bytes:
        """Render a PDF for an already-loaded quote."""
        from app.common.pdf import DocumentPDFGenerator

        owner_info, logo_bytes = self.load_owner_branding(quote)
        return DocumentPDFGenerator().generate_quote_pdf(
            quote, owner=owner_info, logo_bytes=logo_bytes
        )
