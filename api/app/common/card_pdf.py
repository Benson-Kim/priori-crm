"""Branded PDF exports for the vendor Supplier Statements cards (PO-33).

The three cards are tabular lists (no per-document owner snapshot), so this
renderer resolves the *live* owner profile for branding and delegates the
actual layout to the shared ``DocumentPDFGenerator.generate_card_pdf`` (same
header + table styling as the invoice/quote/PO PDFs — DRY).

Each method's column shape mirrors the matching ``ExcelExporter`` method so the
PDF and Excel exports of a card are always in parity.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.common.pdf import DocumentPDFGenerator


def _fmt_date(value: date | None) -> str:
    return value.strftime("%b %d, %Y") if value else "\u2014"


def _fmt_money(value: Decimal | None, currency: str | None = None) -> str:
    amount = Decimal("0.00") if value is None else value
    prefix = f"{currency} " if currency else ""
    return f"{prefix}{amount:,.2f}"


def _range_subtitle(date_from: date | None, date_to: date | None) -> str | None:
    """Human-readable date-range line for the PDF header (or None)."""
    if not date_from and not date_to:
        return None
    start = _fmt_date(date_from) if date_from else "Beginning"
    end = _fmt_date(date_to) if date_to else "Today"
    return f"{start} to {end}"


class CardPdfExporter:
    """Render the vendor Supplier Statements cards to branded PDFs."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def _owner_branding(self) -> tuple[Any, Any]:
        """Resolve (owner_info, logo_bytes) from the live owner profile.

        Cards are not issued documents, so there is no immutable snapshot to
        pin — the current organisation identity is correct for an on-demand
        export.
        """
        from app.modules.owner.service import OwnerService

        owner_service = OwnerService(self._db)
        profile = owner_service.get_or_create()
        return (
            owner_service.to_owner_info(profile),
            owner_service.load_logo_bytes(profile),
        )

    def export_purchase_orders(
        self,
        card: Any,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> bytes:
        """'Total POs' card PDF (PO Ref, Date, Amount, Status)."""
        owner_info, logo_bytes = self._owner_branding()
        headers = ["PO Ref", "Date", "Amount", "Status"]
        rows = [
            [
                r.po_reference,
                _fmt_date(r.order_date),
                _fmt_money(r.amount, r.currency),
                r.derived_status.capitalize(),
            ]
            for r in card.rows
        ]
        return DocumentPDFGenerator().generate_card_pdf(
            title="Total POs",
            subtitle=_range_subtitle(date_from, date_to),
            headers=headers,
            rows=rows,
            column_aligns=["LEFT", "LEFT", "RIGHT", "CENTER"],
            owner=owner_info,
            logo_bytes=logo_bytes,
        )

    def export_payments(
        self,
        card: Any,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> bytes:
        """'Total Payments' card PDF (Date, Invoice #, Ref #, Amount, PO, Doc)."""
        owner_info, logo_bytes = self._owner_branding()
        headers = [
            "Date",
            "Invoice #",
            "Payment Ref #",
            "Amount",
            "PO Ref",
            "Document",
        ]
        rows = [
            [
                _fmt_date(r.payment_date),
                r.invoice_number or "\u2014",
                r.reference or "\u2014",
                _fmt_money(r.amount, r.currency),
                r.po_reference,
                "Yes" if r.has_document else "No",
            ]
            for r in card.rows
        ]
        return DocumentPDFGenerator().generate_card_pdf(
            title="Total Payments",
            subtitle=_range_subtitle(date_from, date_to),
            headers=headers,
            rows=rows,
            column_aligns=["LEFT", "LEFT", "LEFT", "RIGHT", "LEFT", "CENTER"],
            owner=owner_info,
            logo_bytes=logo_bytes,
        )

    def export_invoices(
        self,
        card: Any,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> bytes:
        """'Total Invoices' card PDF (Reference, Date, Amount, Balance, Status)."""
        owner_info, logo_bytes = self._owner_branding()
        headers = ["Reference", "Date", "Amount", "Balance", "Status"]
        rows = [
            [
                r.ref_no,
                _fmt_date(r.invoice_date),
                _fmt_money(r.amount, r.currency),
                _fmt_money(r.balance, r.currency),
                r.status.capitalize(),
            ]
            for r in card.rows
        ]
        return DocumentPDFGenerator().generate_card_pdf(
            title="Total Invoices",
            subtitle=_range_subtitle(date_from, date_to),
            headers=headers,
            rows=rows,
            column_aligns=["LEFT", "LEFT", "RIGHT", "RIGHT", "CENTER"],
            owner=owner_info,
            logo_bytes=logo_bytes,
        )
