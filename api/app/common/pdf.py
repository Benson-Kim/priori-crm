"""
PDF generation for invoices, quotes and purchase orders using ReportLab.

Deep module: small interface (generate_invoice_pdf / generate_quote_pdf /
generate_purchase_order_pdf), concentrated implementation behind it.
"""

import io
import logging
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.lib.config import settings

logger = logging.getLogger(__name__)

# Logo is constrained to this height in the header; width scales to preserve
# the source aspect ratio.
_LOGO_MAX_HEIGHT_MM = 18
_LOGO_MAX_WIDTH_MM = 60

# Colors
_INK = colors.HexColor("#333333")
_PURE_BLACK = colors.black
_MUTED_LABEL = colors.HexColor("#817d7d")
_TABLE_HEADER_BG = colors.HexColor("#2C1A54")
_TABLE_LINE = colors.HexColor("#E3E3E3")

# Reusable styles
_STYLES = getSampleStyleSheet()
_LINE_SPACING = 10.75

_HEADER_STYLE = ParagraphStyle(
    "DocHeader",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=15.5,
    textColor=_PURE_BLACK,
    leading=18,
)
_TITLE_STYLE = ParagraphStyle(
    "DocTitle",
    parent=_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=23,
    textColor=_PURE_BLACK,
    alignment=TA_RIGHT,
    leading=25,
)
_SUB_STYLE = ParagraphStyle(
    "DocSub",
    parent=_STYLES["Normal"],
    fontSize=11.5,
    leading=14,
    textColor=_INK,
)
_LABEL_STYLE = ParagraphStyle(
    "DocLabel",
    parent=_SUB_STYLE,
    fontName="Helvetica-Bold",
    fontSize=11,
    textColor=_PURE_BLACK,
)
_SECTION_LABEL_STYLE = ParagraphStyle(
    "SectionLabel",
    parent=_SUB_STYLE,
    fontName="Helvetica-Bold",
    textColor=_MUTED_LABEL,
    leading=18,
)
_BODY_STYLE = ParagraphStyle(
    "DocBody",
    parent=_STYLES["Normal"],
    fontSize=11.5,
    leading=16,
    textColor=_INK,
)
_ITEM_STYLE = ParagraphStyle(
    "ItemDesc",
    parent=_STYLES["Normal"],
    fontSize=11.5,
    leading=12,
    textColor=_INK,
)
_ITEM_NUM_STYLE = ParagraphStyle(
    "ItemNum",
    parent=_ITEM_STYLE,
    alignment=TA_RIGHT,
)
_ITEM_HEADER_STYLE = ParagraphStyle(
    "ItemHeader",
    parent=_STYLES["Normal"],
    fontSize=11.5,
    textColor=colors.white,
)
_ITEM_HEADER_CENTER_STYLE = ParagraphStyle(
    "ItemHeaderCenter",
    parent=_ITEM_HEADER_STYLE,
    alignment=TA_CENTER,
)
_ITEM_HEADER_RIGHT_STYLE = ParagraphStyle(
    "ItemHeaderRight",
    parent=_ITEM_HEADER_STYLE,
    alignment=TA_RIGHT,
)

_META_VAL_STYLE = ParagraphStyle("MetaVal", parent=_SUB_STYLE, alignment=TA_RIGHT)
_IQ_STYLE = ParagraphStyle("IQ", parent=_ITEM_NUM_STYLE, alignment=TA_CENTER)
_TOTAL_LBL_STYLE = ParagraphStyle("TotalLbl", parent=_SUB_STYLE, alignment=TA_RIGHT)
_VAL_BOLD_STYLE = ParagraphStyle(
    "ValBold", parent=_SUB_STYLE, fontName="Helvetica-Bold", alignment=TA_RIGHT
)
_SUMMARY_VAL_STYLE = ParagraphStyle("SummaryVal", parent=_SUB_STYLE, alignment=TA_RIGHT)
_STMT_NUM_STYLE = ParagraphStyle("StmtNum", parent=_ITEM_STYLE, alignment=TA_RIGHT)
_CLOSING_LBL_STYLE = ParagraphStyle("ClosingLbl", parent=_SUB_STYLE, alignment=TA_RIGHT)
_CLOSING_VAL_STYLE = ParagraphStyle(
    "ClosingVal", parent=_SUB_STYLE, fontName="Helvetica-Bold", alignment=TA_RIGHT
)


def _split_lines(value) -> list[str]:
    """Split a possibly multi-line text field (e.g. a free-text address
    block) into individual non-empty lines for separate Paragraphs."""
    if not value:
        return []
    return [ln.strip() for ln in str(value).splitlines() if ln.strip()]


class DocumentPDFGenerator:
    """Generate branded PDF documents for invoices and quotes."""

    # public interface

    def generate_invoice_pdf(self, invoice, owner=None, logo_bytes=None) -> bytes:
        """Return PDF bytes for an invoice ORM object.

        ``owner`` is an optional OwnerInfo DTO (built from the document's
        immutable snapshot for issued invoices, or the live profile). When
        omitted, the header falls back to ``settings.APP_NAME``. ``logo_bytes``
        is the optional logo image binary embedded in the header.
        """
        return self._build_pdf(
            owner=owner,
            logo_bytes=logo_bytes,
            doc_type="INVOICE",
            reference=invoice.invoice_reference,
            number=invoice.invoice_number,
            customer=invoice.customer,
            transaction_date=invoice.transaction_date,
            due_date=invoice.due_date,
            currency=invoice.currency,
            line_items=invoice.line_items,
            subtotal=invoice.subtotal,
            discount_type=invoice.discount_type,
            discount_amount=invoice.discount_amount,
            discount_percentage=invoice.discount_percentage,
            tax_total=invoice.tax_total,
            total_due=invoice.total_due,
            amount_paid=getattr(invoice, "amount_paid", Decimal("0.00")),
            balance_due=getattr(invoice, "balance_due", None),
            notes=invoice.notes,
        )

    def generate_quote_pdf(self, quote, owner=None, logo_bytes=None) -> bytes:
        """Return PDF bytes for a quote ORM object (optional OwnerInfo header)."""
        return self._build_pdf(
            owner=owner,
            logo_bytes=logo_bytes,
            doc_type="QUOTE",
            reference=quote.quote_reference,
            number=quote.quote_number,
            customer=quote.customer,
            transaction_date=quote.transaction_date,
            due_date=quote.due_date,
            currency=quote.currency,
            line_items=quote.line_items,
            subtotal=quote.subtotal,
            discount_type=quote.discount_type,
            discount_amount=quote.discount_amount,
            discount_percentage=quote.discount_percentage,
            tax_total=quote.tax_total,
            total_due=quote.total_due,
            amount_paid=Decimal("0.00"),
            balance_due=None,
            notes=quote.notes,
        )

    def generate_purchase_order_pdf(
        self, purchase_order, owner=None, logo_bytes=None, include_balance=False
    ) -> bytes:
        """Return PDF bytes for a purchase-order ORM object.

        Vendor-facing document. Distinct from the customer-facing
        invoice/quote layout: it renders a "Vendor Address:" block, the
        "PURCHASE ORDER" label, an Item | Description | Qty | Rate | Amount
        table and a Sub Total / VAT / TOTAL summary (no discount in v1).
        "Rate" and "Amount" are display labels for unit_price / line_total.
        Excludes any edit controls, action buttons or attachments list.

        ``include_balance`` selects the variant: False renders the ORIGINAL
        document (TOTAL only, no payments); True renders the CURRENT /
        statement document, appending Amount Paid + Balance Due rows. The
        two share the same builder so the layouts can never drift.

        ``owner`` is an optional OwnerInfo DTO (the PO's immutable snapshot
        when sent, else the live profile for a Draft preview); ``logo_bytes``
        is the optional header logo binary.
        """
        return self._build_purchase_order_pdf(
            owner=owner,
            logo_bytes=logo_bytes,
            purchase_order=purchase_order,
            include_balance=include_balance,
        )

    # private implementation

    def _build_purchase_order_pdf(
        self,
        *,
        owner,
        logo_bytes: bytes | None,
        purchase_order,
        include_balance: bool = False,
    ) -> bytes:
        # The balance-aware variant is a distinct STATEMENT document (reducing
        # balances), not the PURCHASE ORDER layout, so it is built separately.
        if include_balance:
            return self._build_purchase_order_statement_pdf(
                owner=owner, logo_bytes=logo_bytes, purchase_order=purchase_order
            )

        po = purchase_order
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )
        content_width = doc.width
        elements: list = []

        logo_flowable = self._build_logo(logo_bytes)
        owner_name = getattr(owner, "full_name", None) or settings.APP_NAME

        left_cell: list = []
        if logo_flowable is not None:
            left_cell.append(logo_flowable)
            left_cell.append(Spacer(1, 4 * mm))
        left_cell.append(Paragraph(owner_name, _HEADER_STYLE))
        left_cell.append(Spacer(1, 2 * mm))

        if owner is not None:
            owner_text_fields = (
                getattr(owner, "address", None),
                getattr(owner, "email", None),
                getattr(owner, "phone", None),
                (
                    f"Tax PIN: {owner.tax_pin}"
                    if getattr(owner, "tax_pin", None)
                    else None
                ),
                getattr(owner, "website", None),
            )
            for field in owner_text_fields:
                lines = _split_lines(field)
                for i, line in enumerate(lines):
                    left_cell.append(Paragraph(line, _SUB_STYLE))
                    if i < len(lines) - 1:
                        left_cell.append(Spacer(1, 1.2 * mm))
            if getattr(owner, "location_watermark", None):
                for line in _split_lines(owner.location_watermark):
                    left_cell.append(Paragraph(line, _SUB_STYLE))

        header_table = Table(
            [[left_cell, Paragraph("PURCHASE ORDER", _TITLE_STYLE)]],
            colWidths=[content_width - 85 * mm, 85 * mm],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(header_table)
        elements.append(Spacer(1, 10 * mm))

        vendor = po.vendor
        vendor_name = getattr(vendor, "vendor_name", str(po.vendor_id))
        vendor_cell: list = [
            Paragraph("Vendor Address:", _LABEL_STYLE),
            Spacer(1, 3 * mm),
            Paragraph(str(vendor_name), _SUB_STYLE),
            Spacer(1, 1.5 * mm),
        ]
        for field in (
            getattr(vendor, "address", None),
            getattr(vendor, "email", None),
            getattr(vendor, "phone_primary", None),
        ):
            lines = _split_lines(field)
            for i, line in enumerate(lines):
                vendor_cell.append(Paragraph(line, _SUB_STYLE))
                if i < len(lines) - 1:
                    vendor_cell.append(Spacer(1, 1.5 * mm))

        delivery = po.delivery_date.strftime("%b %d, %Y") if po.delivery_date else "—"
        order_date = po.order_date.strftime("%b %d, %Y") if po.order_date else "—"
        meta_rows = [
            ["PO#", str(po.po_reference)],
            ["Order Date", order_date],
            ["Delivery Date", delivery],
        ]
        if po.compliance_ref:
            meta_rows.append(["Compliance Ref", str(po.compliance_ref)])

        meta_inner = Table(
            [
                [Paragraph(label, _LABEL_STYLE), Paragraph(value, _META_VAL_STYLE)]
                for label, value in meta_rows
            ],
            colWidths=[32.5 * mm, 30 * mm],
        )
        meta_inner.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )

        vm_table = Table(
            [[vendor_cell, meta_inner]],
            colWidths=[content_width - 62.5 * mm, 62.5 * mm],
        )
        vm_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(vm_table)
        elements.append(Spacer(1, 8 * mm))

        header_row = [
            Paragraph("Item Description", _ITEM_HEADER_STYLE),
            Paragraph("Qty", _ITEM_HEADER_CENTER_STYLE),
            Paragraph("Rate", _ITEM_HEADER_CENTER_STYLE),
            Paragraph("Amount", _ITEM_HEADER_RIGHT_STYLE),
        ]
        rows = [header_row]
        for item in po.line_items:
            description = getattr(item, "description", None)
            label = (
                f"{item.item_name} ({description})"
                if description
                else str(item.item_name)
            )
            rows.append(
                [
                    Paragraph(label, _ITEM_STYLE),
                    Paragraph(f"{item.quantity:,.2f}", _IQ_STYLE),
                    Paragraph(f"{item.unit_price:,.2f}", _IQ_STYLE),
                    Paragraph(f"{item.line_total:,.2f}", _ITEM_NUM_STYLE),
                ]
            )

        items_table = Table(
            rows,
            colWidths=[content_width - 75 * mm, 20 * mm, 25 * mm, 30 * mm],
            repeatRows=1,
        )
        items_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _TABLE_HEADER_BG),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.5, _TABLE_LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (3, 0), (3, -1), 8),
                ]
            )
        )
        elements.append(items_table)
        elements.append(Spacer(1, 5 * mm))

        # PO-level VAT (PO-27): render "VAT {rate}% ({compliance ref})" from the
        # PO's own fields. vat_rate is a fraction (e.g. 0.16 -> "16"); the
        # compliance ref (defaulted from the owner's tax PIN) is appended when
        # present. Falls back to a bare "VAT" label when VAT is disabled.
        if getattr(po, "vat_enabled", False) and getattr(po, "vat_rate", None):
            pct = (po.vat_rate * 100).normalize()
            ref = f" ({po.vat_compliance_ref})" if po.vat_compliance_ref else ""
            vat_label = f"VAT {pct}%{ref}"
        else:
            vat_label = "VAT"

        totals_rows = [
            [
                Paragraph("Sub Total", _TOTAL_LBL_STYLE),
                Paragraph(f"{po.subtotal:,.2f}", _VAL_BOLD_STYLE),
            ],
            [
                Paragraph(vat_label, _TOTAL_LBL_STYLE),
                Paragraph(f"{po.tax_total:,.2f}", _VAL_BOLD_STYLE),
            ],
            [
                Paragraph("TOTAL", _TOTAL_LBL_STYLE),
                Paragraph(f"{po.currency} {po.total:,.2f}", _VAL_BOLD_STYLE),
            ],
        ]
        totals_wrapper = Table(
            [[Spacer(1, 1), Table(totals_rows, colWidths=[32.5 * mm, 30 * mm])]],
            colWidths=[content_width - 62.5 * mm, 62.5 * mm],
        )
        totals_wrapper.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(totals_wrapper)

        for title, content in [
            ("Notes", po.notes),
            ("Terms & Conditions", po.terms_and_conditions),
        ]:
            if content:
                elements.append(Spacer(1, 10 * mm))
                elements.append(Paragraph(title, _SECTION_LABEL_STYLE))
                elements.append(Spacer(1, 3 * mm))
                lines = _split_lines(content)
                for i, ln in enumerate(lines):
                    elements.append(Paragraph(ln, _BODY_STYLE))
                    if i < len(lines) - 1:
                        elements.append(Spacer(1, 2 * mm))

        doc.build(elements)
        pdf_bytes = buf.getvalue()
        buf.close()

        logger.info(
            "Generated purchase_order PDF: %s (%d bytes)",
            po.po_reference,
            len(pdf_bytes),
        )
        return pdf_bytes

    def _po_header_and_meta(
        self, *, owner, logo_bytes, po, content_width, doc_type_label: str
    ) -> list:
        """Build the shared header + vendor/metadata flowables for a PO document.

        Returns the elements list (logo + owner identity on the left, the
        document title on the right, then the vendor address block beside the
        PO metadata). Shared by the PURCHASE ORDER and STATEMENT layouts so the
        owner and vendor blocks render identically across both.
        """
        elements: list = []

        logo_flowable = self._build_logo(logo_bytes)
        owner_name = getattr(owner, "full_name", None) or settings.APP_NAME

        left_cell: list = []
        if logo_flowable is not None:
            left_cell.append(logo_flowable)
            left_cell.append(Spacer(1, 4 * mm))
        left_cell.append(Paragraph(owner_name, _HEADER_STYLE))
        left_cell.append(Spacer(1, 2 * mm))

        if owner is not None:
            owner_text_fields = (
                getattr(owner, "address", None),
                getattr(owner, "email", None),
                getattr(owner, "phone", None),
                (
                    f"Tax PIN: {owner.tax_pin}"
                    if getattr(owner, "tax_pin", None)
                    else None
                ),
                getattr(owner, "website", None),
            )
            for field in owner_text_fields:
                lines = _split_lines(field)
                for i, line in enumerate(lines):
                    left_cell.append(Paragraph(line, _SUB_STYLE))
                    if i < len(lines) - 1:
                        left_cell.append(Spacer(1, 1.2 * mm))
            if getattr(owner, "location_watermark", None):
                for line in _split_lines(owner.location_watermark):
                    left_cell.append(Paragraph(line, _SUB_STYLE))

        header_table = Table(
            [[left_cell, Paragraph(doc_type_label, _TITLE_STYLE)]],
            colWidths=[content_width - 85 * mm, 85 * mm],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(header_table)
        elements.append(Spacer(1, 10 * mm))

        vendor = po.vendor
        vendor_name = getattr(vendor, "vendor_name", str(po.vendor_id))
        vendor_cell: list = [
            Paragraph("Vendor Address:", _LABEL_STYLE),
            Spacer(1, 3 * mm),
            Paragraph(str(vendor_name), _SUB_STYLE),
            Spacer(1, 1.5 * mm),
        ]
        for field in (
            getattr(vendor, "address", None),
            getattr(vendor, "email", None),
            getattr(vendor, "phone_primary", None),
        ):
            lines = _split_lines(field)
            for i, line in enumerate(lines):
                vendor_cell.append(Paragraph(line, _SUB_STYLE))
                if i < len(lines) - 1:
                    vendor_cell.append(Spacer(1, 1.2 * mm))

        delivery = po.delivery_date.strftime("%b %d, %Y") if po.delivery_date else "—"
        order_date = po.order_date.strftime("%b %d, %Y") if po.order_date else "—"
        meta_rows = [
            ["PO#", str(po.po_reference)],
            ["Order Date", order_date],
            ["Delivery Date", delivery],
        ]
        if po.compliance_ref:
            meta_rows.append(["Compliance Ref", str(po.compliance_ref)])

        meta_inner = Table(
            [
                [Paragraph(label, _LABEL_STYLE), Paragraph(value, _META_VAL_STYLE)]
                for label, value in meta_rows
            ],
            colWidths=[32.5 * mm, 30 * mm],
        )
        meta_inner.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )

        vm_table = Table(
            [[vendor_cell, meta_inner]],
            colWidths=[content_width - 62.5 * mm, 62.5 * mm],
        )
        vm_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(vm_table)
        elements.append(Spacer(1, 8 * mm))
        return elements

    def _build_purchase_order_statement_pdf(
        self,
        *,
        owner,
        logo_bytes: bytes | None,
        purchase_order,
    ) -> bytes:
        """Render the STATEMENT variant of a purchase order (reducing balances).

        Layout:
          - title "STATEMENT"; same owner + vendor + PO metadata blocks as the
            PURCHASE ORDER document;
          - a SUMMARY section: bold labels / regular values for PO Amount,
            Paid and Balance;
          - a Date | Details | Amount | Balance table whose first row is the
            billed invoice for the full PO amount (Balance = PO amount) and
            whose subsequent rows are the recorded payments, each reducing the
            running balance; a final "Balance:" row shows the closing balance.
        """
        po = purchase_order
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )
        content_width = doc.width

        elements = self._po_header_and_meta(
            owner=owner,
            logo_bytes=logo_bytes,
            po=po,
            content_width=content_width,
            doc_type_label="PURCHASE ORDER STATEMENT",
        )

        currency = po.currency
        total = po.total
        amount_paid = getattr(po, "amount_paid", Decimal("0.00"))
        balance = getattr(po, "balance_due", total - amount_paid)

        # SUMMARY
        summary_rows = [
            [Paragraph("SUMMARY", _SECTION_LABEL_STYLE), ""],
            [
                Paragraph("PO Amount", _LABEL_STYLE),
                Paragraph(f"{currency} {total:,.2f}", _SUMMARY_VAL_STYLE),
            ],
            [
                Paragraph("Paid", _LABEL_STYLE),
                Paragraph(f"{currency} {amount_paid:,.2f}", _SUMMARY_VAL_STYLE),
            ],
            [
                Paragraph("Balance", _LABEL_STYLE),
                Paragraph(f"{currency} {balance:,.2f}", _SUMMARY_VAL_STYLE),
            ],
        ]
        summary_table = Table(
            summary_rows, colWidths=[32.5 * mm, 30 * mm], hAlign="RIGHT"
        )
        summary_table.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 0), (1, 0)),  # title spans both columns
                    ("BOTTOMPADDING", (0, 0), (1, 0), 2 * mm),  # space under title
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
                ]
            )
        )

        elements.append(summary_table)
        elements.append(Spacer(1, 8 * mm))

        header_row = [
            Paragraph("Date", _ITEM_HEADER_STYLE),
            Paragraph("Details", _ITEM_HEADER_STYLE),
            Paragraph("Amount", _ITEM_HEADER_RIGHT_STYLE),
            Paragraph("Balance", _ITEM_HEADER_RIGHT_STYLE),
        ]
        rows = [header_row]

        # Billed-invoice opening row: Details references the line items as
        # "Item name (description)"; the amount and opening balance are the
        # full PO total.
        line_details = (
            "; ".join(
                (
                    f"{item.item_name} ({item.description})"
                    if getattr(item, "description", None)
                    else str(item.item_name)
                )
                for item in po.line_items
            )
            or "Purchase order billed"
        )
        billed_date = po.order_date.strftime("%b %d, %Y") if po.order_date else "—"
        running_balance = total
        rows.append(
            [
                Paragraph(billed_date, _ITEM_STYLE),
                Paragraph(line_details, _ITEM_STYLE),
                Paragraph(f"{total:,.2f}", _STMT_NUM_STYLE),
                Paragraph(f"{running_balance:,.2f}", _STMT_NUM_STYLE),
            ]
        )

        for payment in po.payments:
            running_balance -= payment.amount
            pay_date = (
                payment.payment_date.strftime("%b %d, %Y")
                if payment.payment_date
                else "—"
            )
            detail = (
                f"Payment ({payment.reference})"
                if getattr(payment, "reference", None)
                else "Payment"
            )
            rows.append(
                [
                    Paragraph(pay_date, _ITEM_STYLE),
                    Paragraph(detail, _ITEM_STYLE),
                    Paragraph(f"-{payment.amount:,.2f}", _STMT_NUM_STYLE),
                    Paragraph(f"{running_balance:,.2f}", _STMT_NUM_STYLE),
                ]
            )

        stmt_table = Table(
            rows,
            colWidths=[32.5 * mm, content_width - 92.5 * mm, 30 * mm, 30 * mm],
            repeatRows=1,
        )

        stmt_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _TABLE_HEADER_BG),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.5, _TABLE_LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elements.append(stmt_table)
        elements.append(Spacer(1, 6 * mm))

        closing = Table(
            [
                [
                    Paragraph("Balance:", _CLOSING_LBL_STYLE),
                    Paragraph(f"{currency} {balance:,.2f}", _CLOSING_VAL_STYLE),
                ]
            ],
            colWidths=[content_width - 30 * mm, 30 * mm],
        )
        closing.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elements.append(closing)

        doc.build(elements)
        pdf_bytes = buf.getvalue()
        buf.close()

        logger.info(
            "Generated purchase_order STATEMENT PDF: %s (%d bytes)",
            po.po_reference,
            len(pdf_bytes),
        )
        return pdf_bytes

    def _build_pdf(
        self,
        *,
        owner,
        logo_bytes: bytes | None = None,
        doc_type: str,
        reference: str,
        number: str,
        customer,
        transaction_date: date,
        due_date: date,
        currency: str,
        line_items,
        subtotal: Decimal,
        discount_type: str | None,
        discount_amount: Decimal | None,
        discount_percentage: Decimal | None,
        tax_total: Decimal,
        total_due: Decimal,
        amount_paid: Decimal,
        balance_due: Decimal | None,
        notes: str | None,
    ) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )
        elements: list = []

        # header — optional logo, then owner identity from the injected DTO
        # (snapshot for issued documents), falling back to the app name when
        # none is supplied.
        logo_flowable = self._build_logo(logo_bytes)
        if logo_flowable is not None:
            elements.append(logo_flowable)
            elements.append(Spacer(1, 3 * mm))

        owner_name = getattr(owner, "full_name", None) or settings.APP_NAME
        elements.append(Paragraph(owner_name, _HEADER_STYLE))

        if owner is not None:
            owner_lines = [
                line
                for line in (
                    getattr(owner, "address", None),
                    getattr(owner, "email", None),
                    getattr(owner, "phone", None),
                    (
                        f"Tax PIN: {owner.tax_pin}"
                        if getattr(owner, "tax_pin", None)
                        else None
                    ),
                    getattr(owner, "website", None),
                )
                if line
            ]
            for line in owner_lines:
                elements.append(Paragraph(str(line), _SUB_STYLE))
            if getattr(owner, "location_watermark", None):
                elements.append(Paragraph(str(owner.location_watermark), _SUB_STYLE))

        elements.append(Paragraph(f"{doc_type}  •  {reference}", _SUB_STYLE))
        elements.append(Spacer(1, 6 * mm))

        # metadata table
        customer_name = getattr(customer, "display_name", str(customer))
        meta_data = [
            ["Document #", number, "Customer", customer_name],
            [
                "Date",
                transaction_date.strftime("%d %b %Y"),
                "Due Date",
                due_date.strftime("%d %b %Y"),
            ],
            ["Currency", currency, "", ""],
        ]
        meta_table = Table(meta_data, colWidths=[25 * mm, 55 * mm, 25 * mm, 55 * mm])
        meta_table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6B7280")),
                    ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#6B7280")),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                    ("FONTNAME", (3, 0), (3, -1), "Helvetica-Bold"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(meta_table)
        elements.append(Spacer(1, 8 * mm))

        # line items table
        header_row = ["#", "Item", "Description", "Qty", "Unit Price", "Tax", "Total"]
        rows = [header_row]
        for item in line_items:
            rows.append(
                [
                    str(item.line_number),
                    str(item.item_name),
                    str(item.description)[:40],
                    f"{item.quantity:,.2f}",
                    f"{item.unit_price:,.2f}",
                    str(item.tax_type),
                    f"{item.line_total:,.2f}",
                ]
            )

        col_widths = [8 * mm, 35 * mm, 40 * mm, 18 * mm, 22 * mm, 18 * mm, 22 * mm]
        items_table = Table(rows, colWidths=col_widths, repeatRows=1)
        items_table.setStyle(
            TableStyle(
                [
                    # header
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A1A2E")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 8),
                    # body
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F9FAFB")],
                    ),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                    ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(items_table)
        elements.append(Spacer(1, 6 * mm))

        # totals
        totals_rows = [["Subtotal", f"{currency} {subtotal:,.2f}"]]
        if discount_type and (discount_amount or discount_percentage):
            from app.common.financial import calculate_discount
            from app.constants.enums import DiscountType

            discount_value = calculate_discount(
                subtotal,
                DiscountType(discount_type),
                discount_amount,
                discount_percentage,
            )
            if discount_value:
                label = (
                    f"Discount ({discount_percentage}%)"
                    if discount_type == "percentage" and discount_percentage
                    else "Discount"
                )
                totals_rows.append([label, f"- {currency} {discount_value:,.2f}"])

        totals_rows.append(["Tax", f"{currency} {tax_total:,.2f}"])
        totals_rows.append(["Total Due", f"{currency} {total_due:,.2f}"])
        if amount_paid and amount_paid > 0:
            totals_rows.append(["Amount Paid", f"{currency} {amount_paid:,.2f}"])
        if balance_due is not None:
            totals_rows.append(["Balance Due", f"{currency} {balance_due:,.2f}"])

        totals_table = Table(totals_rows, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
        totals_table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#1A1A2E")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(totals_table)

        # notes
        if notes:
            elements.append(Spacer(1, 8 * mm))
            elements.append(Paragraph("Notes", _SUB_STYLE))
            elements.append(Spacer(1, 2 * mm))
            elements.append(Paragraph(notes, _BODY_STYLE))

        # footer
        elements.append(Spacer(1, 12 * mm))
        footer_text = f"Generated by {owner_name} • {date.today().strftime('%d %b %Y')}"
        elements.append(Paragraph(footer_text, _SUB_STYLE))

        doc.build(elements)
        pdf_bytes = buf.getvalue()
        buf.close()

        logger.info(
            "Generated %s PDF: %s (%d bytes)",
            doc_type.lower(),
            reference,
            len(pdf_bytes),
        )
        return pdf_bytes

    @staticmethod
    def _build_logo(logo_bytes: bytes | None) -> Image | None:
        """Build a header logo flowable from raw image bytes.

        Scales the image to fit within the configured max box while
        preserving aspect ratio. Returns ``None`` (text-only header) when no
        bytes are supplied or the image cannot be decoded, so a corrupt or
        unsupported logo never breaks document generation.
        """
        if not logo_bytes:
            return None
        try:
            reader = ImageReader(io.BytesIO(logo_bytes))
            src_w, src_h = reader.getSize()
            if src_w <= 0 or src_h <= 0:
                return None
            max_w = _LOGO_MAX_WIDTH_MM * mm
            max_h = _LOGO_MAX_HEIGHT_MM * mm
            scale = min(max_w / src_w, max_h / src_h)
            img = Image(
                io.BytesIO(logo_bytes),
                width=src_w * scale,
                height=src_h * scale,
            )
            img.hAlign = "LEFT"
            return img
        except Exception:
            logger.warning("Owner logo could not be embedded in PDF", exc_info=True)
            return None
