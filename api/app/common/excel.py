"""
Excel export for invoices, quotes, and expenses using openpyxl.

Deep module: small interface (3 public methods), concentrated
spreadsheet formatting behind it.
"""

import io
import logging
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

# Shared styles
_HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=10)
_HEADER_FILL = PatternFill(start_color="1A1A2E", end_color="1A1A2E", fill_type="solid")
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
_BODY_FONT = Font(name="Calibri", size=10)
_MONEY_FORMAT = "#,##0.00"
_DATE_FORMAT = "DD MMM YYYY"
_THIN_BORDER = Border(
    bottom=Side(style="thin", color="E5E7EB"),
)


class ExcelExporter:
    """Generate branded Excel workbooks for document exports."""

    # public interface

    def export_invoices(
        self,
        invoices: list,
        include_line_items: bool = False,
    ) -> bytes:
        """Export invoice records to .xlsx bytes."""
        headers = [
            "Invoice #",
            "Reference",
            "Customer",
            "Date",
            "Due Date",
            "Status",
            "Currency",
            "Subtotal",
            "Tax",
            "Total Due",
            "Amount Paid",
            "Balance Due",
        ]

        def row_fn(inv):
            return [
                inv.invoice_number,
                inv.invoice_reference,
                getattr(inv.customer, "display_name", str(inv.customer_id)),
                inv.transaction_date,
                inv.due_date,
                inv.status,
                inv.currency,
                inv.subtotal,
                inv.tax_total,
                inv.total_due,
                inv.amount_paid,
                inv.balance_due,
            ]

        money_cols = [8, 9, 10, 11, 12]  # 1-indexed: Subtotal..Balance
        date_cols = [4, 5]

        wb = self._build_workbook(
            sheet_name="Invoices",
            headers=headers,
            records=invoices,
            row_fn=row_fn,
            money_cols=money_cols,
            date_cols=date_cols,
        )

        if include_line_items and invoices:
            self._add_line_items_sheet(wb, invoices, "invoice")

        return self._to_bytes(wb)

    def export_quotes(
        self,
        quotes: list,
        include_line_items: bool = False,
    ) -> bytes:
        """Export quote records to .xlsx bytes."""
        headers = [
            "Quote #",
            "Reference",
            "Customer",
            "Date",
            "Due Date",
            "Status",
            "Currency",
            "Subtotal",
            "Tax",
            "Total Due",
        ]

        def row_fn(q):
            return [
                q.quote_number,
                q.quote_reference,
                getattr(q.customer, "display_name", str(q.customer_id)),
                q.transaction_date,
                q.due_date,
                q.status,
                q.currency,
                q.subtotal,
                q.tax_total,
                q.total_due,
            ]

        wb = self._build_workbook(
            sheet_name="Quotes",
            headers=headers,
            records=quotes,
            row_fn=row_fn,
            money_cols=[8, 9, 10],
            date_cols=[4, 5],
        )

        if include_line_items and quotes:
            self._add_line_items_sheet(wb, quotes, "quote")

        return self._to_bytes(wb)

    def export_expenses(
        self,
        expenses: list,
        include_line_items: bool = False,
    ) -> bytes:
        """Export expense records to .xlsx bytes."""
        headers = [
            "Expense #",
            "Reference",
            "Vendor",
            "Date",
            "Due Date",
            "Status",
            "Currency",
            "Subtotal",
            "Tax",
            "Total Due",
            "Amount Paid",
            "Balance Due",
        ]

        def row_fn(exp):
            vendor_name = getattr(exp, "vendor_name", None)
            if not vendor_name and hasattr(exp, "vendor"):
                vendor_name = getattr(exp.vendor, "vendor_name", str(exp.vendor_id))
            return [
                exp.expense_number,
                exp.expense_reference,
                vendor_name or str(exp.vendor_id),
                exp.expense_date,
                exp.due_date,
                exp.status,
                exp.currency,
                exp.subtotal,
                exp.tax_total,
                exp.total_due,
                exp.amount_paid,
                exp.balance_due,
            ]

        wb = self._build_workbook(
            sheet_name="Expenses",
            headers=headers,
            records=expenses,
            row_fn=row_fn,
            money_cols=[8, 9, 10, 11, 12],
            date_cols=[4, 5],
        )

        if include_line_items and expenses:
            self._add_line_items_sheet(wb, expenses, "expense")

        return self._to_bytes(wb)

    def export_purchase_orders(
        self,
        purchase_orders: list,
        include_line_items: bool = False,
    ) -> bytes:
        """Export purchase-order records to .xlsx bytes.

        Mirrors export_expenses (vendor-facing). No discount in v1, so the
        money columns are Subtotal / Tax / Total only.
        """
        headers = [
            "PO #",
            "Reference",
            "Vendor",
            "Order Date",
            "Delivery Date",
            "Status",
            "Currency",
            "Subtotal",
            "Tax",
            "Total",
        ]

        def row_fn(po):
            vendor_name = getattr(po, "vendor_name", None)
            if not vendor_name and hasattr(po, "vendor"):
                vendor_name = getattr(po.vendor, "vendor_name", str(po.vendor_id))
            return [
                po.po_number,
                po.po_reference,
                vendor_name or str(po.vendor_id),
                po.order_date,
                po.delivery_date,
                po.status,
                po.currency,
                po.subtotal,
                po.tax_total,
                po.total,
            ]

        wb = self._build_workbook(
            sheet_name="Purchase Orders",
            headers=headers,
            records=purchase_orders,
            row_fn=row_fn,
            money_cols=[8, 9, 10],
            date_cols=[4, 5],
        )

        if include_line_items and purchase_orders:
            self._add_line_items_sheet(wb, purchase_orders, "purchase_order")

        return self._to_bytes(wb)

    # private implementation

    def _build_workbook(
        self,
        *,
        sheet_name: str,
        headers: list[str],
        records: list,
        row_fn,
        money_cols: list[int],
        date_cols: list[int],
    ) -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name

        # write header row
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _HEADER_ALIGN

        # write data rows
        for row_idx, record in enumerate(records, 2):
            values = row_fn(record)
            for col_idx, value in enumerate(values, 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = _BODY_FONT
                cell.border = _THIN_BORDER

                if isinstance(value, Decimal):
                    cell.value = float(value)
                elif isinstance(value, (date, datetime)):
                    cell.value = value
                else:
                    cell.value = str(value) if value is not None else ""

                if col_idx in money_cols:
                    cell.number_format = _MONEY_FORMAT
                    cell.alignment = Alignment(horizontal="right")
                elif col_idx in date_cols:
                    cell.number_format = _DATE_FORMAT

        # auto-fit column widths
        for col_idx in range(1, len(headers) + 1):
            letter = get_column_letter(col_idx)
            max_len = len(headers[col_idx - 1])
            for row_idx in range(2, min(len(records) + 2, 52)):  # sample first 50 rows
                cell_val = ws.cell(row=row_idx, column=col_idx).value
                if cell_val:
                    max_len = max(max_len, len(str(cell_val)))
            ws.column_dimensions[letter].width = min(max_len + 3, 40)

        # freeze header row
        ws.freeze_panes = "A2"

        logger.info(
            "Built Excel sheet '%s' with %d records",
            sheet_name,
            len(records),
        )
        return wb

    def _add_line_items_sheet(
        self,
        wb: Workbook,
        records: list,
        doc_type: str,
    ) -> None:
        """Add a second sheet with line item details."""
        ws = wb.create_sheet("Line Items")

        headers = [
            f"{doc_type.title()} #",
            "Line #",
            "Item",
            "Description",
            "Qty",
            "Unit Price",
            "Tax Type",
            "Tax Amount",
            "Line Total",
        ]
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _HEADER_ALIGN

        row_idx = 2
        for record in records:
            number_field = f"{doc_type}_number"
            doc_number = getattr(record, number_field, "")
            for item in getattr(record, "line_items", []):
                ws.cell(row=row_idx, column=1, value=doc_number).font = _BODY_FONT
                ws.cell(row=row_idx, column=2, value=item.line_number).font = _BODY_FONT
                ws.cell(row=row_idx, column=3, value=item.item_name).font = _BODY_FONT
                ws.cell(row=row_idx, column=4, value=item.description).font = _BODY_FONT

                qty_cell = ws.cell(row=row_idx, column=5, value=float(item.quantity))
                qty_cell.number_format = _MONEY_FORMAT
                qty_cell.font = _BODY_FONT

                price_cell = ws.cell(
                    row=row_idx, column=6, value=float(item.unit_price)
                )
                price_cell.number_format = _MONEY_FORMAT
                price_cell.font = _BODY_FONT

                ws.cell(
                    row=row_idx, column=7, value=str(item.tax_type)
                ).font = _BODY_FONT

                tax_cell = ws.cell(row=row_idx, column=8, value=float(item.tax_amount))
                tax_cell.number_format = _MONEY_FORMAT
                tax_cell.font = _BODY_FONT

                total_cell = ws.cell(
                    row=row_idx, column=9, value=float(item.line_total)
                )
                total_cell.number_format = _MONEY_FORMAT
                total_cell.font = _BODY_FONT

                row_idx += 1

        ws.freeze_panes = "A2"

    @staticmethod
    def _to_bytes(wb: Workbook) -> bytes:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()
