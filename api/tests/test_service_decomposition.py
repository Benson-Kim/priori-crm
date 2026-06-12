"""decomposition wiring tests.

Statistics SQL, export loading and PDF orchestration live in dedicated
objects (*StatisticsRepository, *ExportQuery, DocumentPdfRenderer); the
document services keep thin delegating methods. The heavy SQL itself is
exercised end-to-end by the existing statistics/export tests through those
delegating methods — these tests pin the wiring so the decomposition
cannot silently regress.
"""

import pytest

from app.common.pdf_renderer import DocumentPdfRenderer
from app.modules.expenses import queries as expense_queries
from app.modules.expenses.service import ExpenseService
from app.modules.invoices import queries as invoice_queries
from app.modules.invoices.service import InvoiceService
from app.modules.quotes import queries as quote_queries
from app.modules.quotes.service import QuoteService

# Pure wiring checks — no schema needed.
pytestmark = pytest.mark.no_db


class TestStatisticsDelegation:
    def test_invoice_counts_and_stats_route_through_repository(self, monkeypatch):
        counts, stats = object(), object()
        monkeypatch.setattr(
            invoice_queries.InvoiceStatisticsRepository,
            "status_counts",
            lambda self, customer_id=None: counts,
        )
        monkeypatch.setattr(
            invoice_queries.InvoiceStatisticsRepository,
            "statistics",
            lambda self, date_from=None, date_to=None: stats,
        )
        service = InvoiceService(None)
        assert service.get_status_counts() is counts
        assert service.get_invoice_statistics() is stats

    def test_quote_counts_and_stats_route_through_repository(self, monkeypatch):
        counts, stats = object(), object()
        monkeypatch.setattr(
            quote_queries.QuoteStatisticsRepository,
            "status_counts",
            lambda self: counts,
        )
        monkeypatch.setattr(
            quote_queries.QuoteStatisticsRepository,
            "statistics",
            lambda self, date_from=None, date_to=None: stats,
        )
        service = QuoteService(None)
        assert service.get_status_counts() is counts
        assert service.get_quote_statistics() is stats

    def test_expense_counts_and_stats_route_through_repository(self, monkeypatch):
        counts, stats = object(), object()
        monkeypatch.setattr(
            expense_queries.ExpenseStatisticsRepository,
            "status_counts",
            lambda self: counts,
        )
        monkeypatch.setattr(
            expense_queries.ExpenseStatisticsRepository,
            "statistics",
            lambda self, date_from=None, date_to=None: stats,
        )
        service = ExpenseService(None)
        assert service.get_status_counts() is counts
        assert service.get_statistics() is stats


class TestExportDelegation:
    def test_each_service_routes_export_through_its_query_object(self, monkeypatch):
        rows = [object()]

        def fake_list(self, filters=None, include_line_items=False, limit=None):
            return rows

        monkeypatch.setattr(invoice_queries.InvoiceExportQuery, "list", fake_list)
        monkeypatch.setattr(quote_queries.QuoteExportQuery, "list", fake_list)
        monkeypatch.setattr(expense_queries.ExpenseExportQuery, "list", fake_list)

        assert InvoiceService(None).list_for_export() is rows
        assert QuoteService(None).list_for_export() is rows
        assert ExpenseService(None).list_for_export() is rows


class TestSharedFilterFunction:
    def test_services_share_the_module_level_filter_appliers(self):
        """list view and export apply the SAME function,
        so the Excel export can never drift from the list view."""
        assert InvoiceService._apply_filters is invoice_queries.apply_invoice_filters
        assert QuoteService._apply_filters is quote_queries.apply_quote_filters
        assert ExpenseService._apply_filters is expense_queries.apply_expense_filters


class TestPdfRendererDelegation:
    def test_invoice_and_quote_render_through_shared_renderer(self, monkeypatch):
        monkeypatch.setattr(
            DocumentPdfRenderer, "render_invoice", lambda self, entity: b"INV"
        )
        monkeypatch.setattr(
            DocumentPdfRenderer, "render_quote", lambda self, entity: b"QTE"
        )
        assert InvoiceService(None)._render_pdf(object()) == b"INV"
        assert QuoteService(None)._render_pdf(object()) == b"QTE"
