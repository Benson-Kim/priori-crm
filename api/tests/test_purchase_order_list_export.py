"""list view, status counts and Excel export.

The list/counts/export paths use the summary projection, a single
vendor join, a grouped COUNT and ``with_for_update``-free reads; run them
against Postgres so UUID columns, server defaults and the index-backed
ordering match production.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.common.pagination import PaginationParams
from app.constants.enums import Currency, PurchaseOrderStatus, TaxType, VendorStatus
from app.modules.purchase_orders.models import PurchaseOrder, PurchaseOrderLineItem
from app.modules.purchase_orders.schemas import PurchaseOrderFilterParams
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="PO list/counts/export use Postgres UUID columns + server defaults.",
)


# HELPERS


def _vendor(db, *, name="Acme Supplies", email="acme@vendor.test") -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        email=email,
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _po(
    db,
    vendor,
    *,
    number,
    reference,
    status=PurchaseOrderStatus.DRAFT,
    order_date=None,
    delivery_date=None,
    total=Decimal("116.00"),
    with_line=True,
) -> PurchaseOrder:
    order_date = order_date or date.today()
    po = PurchaseOrder(
        po_number=number,
        po_reference=reference,
        vendor_id=vendor.id,
        order_date=order_date,
        delivery_date=delivery_date,
        currency=Currency.KES,
        status=status,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("16.00"),
        total=total,
    )
    db.add(po)
    db.flush()
    if with_line:
        db.add(
            PurchaseOrderLineItem(
                po_id=po.id,
                line_number=1,
                item_name="Steel bolts",
                description="Box of 500",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                tax_type=TaxType.VAT_16,
                tax_amount=Decimal("16.00"),
                line_total=Decimal("100.00"),
            )
        )
        db.flush()
    return po


def _seed_one_per_status(db, vendor) -> dict[str, PurchaseOrder]:
    today = date.today().strftime("%Y%m%d")
    return {
        "draft": _po(
            db,
            vendor,
            number=f"PO-{today}-001",
            reference="PO-000001",
            status=PurchaseOrderStatus.DRAFT,
        ),
        "sent": _po(
            db,
            vendor,
            number=f"PO-{today}-002",
            reference="PO-000002",
            status=PurchaseOrderStatus.SENT,
        ),
        "billed": _po(
            db,
            vendor,
            number=f"PO-{today}-003",
            reference="PO-000003",
            status=PurchaseOrderStatus.BILLED,
        ),
        "canceled": _po(
            db,
            vendor,
            number=f"PO-{today}-004",
            reference="PO-000004",
            status=PurchaseOrderStatus.CANCELED,
        ),
    }


class _QueryCounter:
    """Count SQL statements executed on a session's bind."""

    def __init__(self, db):
        self._engine = db.get_bind()
        self.count = 0

    def _on_execute(self, *args, **kwargs):
        self.count += 1

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._on_execute)


# FILTERS


def test_list_filters_by_status(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    _seed_one_per_status(db, vendor)

    result = service.list_purchase_orders(
        PaginationParams(), PurchaseOrderFilterParams(status=PurchaseOrderStatus.SENT)
    )
    statuses = {item.status for item in result.items}
    assert statuses == {PurchaseOrderStatus.SENT.value}


def test_list_hides_canceled_by_default(db):
    """CANCELED is hidden unless explicitly requested (shared visibility rule)."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    seeded = _seed_one_per_status(db, vendor)

    result = service.list_purchase_orders(
        PaginationParams(), PurchaseOrderFilterParams()
    )
    ids = {item.id for item in result.items}
    assert seeded["canceled"].id not in ids
    assert seeded["draft"].id in ids
    assert seeded["sent"].id in ids
    assert seeded["billed"].id in ids


def test_list_filters_by_vendor(db):
    service = PurchaseOrderService(db)
    today = date.today().strftime("%Y%m%d")
    v1 = _vendor(db, name="Vendor One", email="one@vendor.test")
    v2 = _vendor(db, name="Vendor Two", email="two@vendor.test")
    _po(db, v1, number=f"PO-{today}-001", reference="PO-000001")
    target = _po(db, v2, number=f"PO-{today}-002", reference="PO-000002")

    result = service.list_purchase_orders(
        PaginationParams(), PurchaseOrderFilterParams(vendor_id=v2.id)
    )
    ids = {item.id for item in result.items}
    assert ids == {target.id}


def test_list_filters_by_order_date_range(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today()
    stamp = today.strftime("%Y%m%d")
    old = _po(
        db,
        vendor,
        number=f"PO-{stamp}-001",
        reference="PO-000001",
        order_date=today - timedelta(days=40),
    )
    recent = _po(
        db,
        vendor,
        number=f"PO-{stamp}-002",
        reference="PO-000002",
        order_date=today - timedelta(days=5),
    )

    result = service.list_purchase_orders(
        PaginationParams(),
        PurchaseOrderFilterParams(date_from=today - timedelta(days=10)),
    )
    ids = {item.id for item in result.items}
    assert recent.id in ids
    assert old.id not in ids


def test_list_filters_by_delivery_date_range(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today()
    stamp = today.strftime("%Y%m%d")
    near = _po(
        db,
        vendor,
        number=f"PO-{stamp}-001",
        reference="PO-000001",
        delivery_date=today + timedelta(days=3),
    )
    far = _po(
        db,
        vendor,
        number=f"PO-{stamp}-002",
        reference="PO-000002",
        delivery_date=today + timedelta(days=60),
    )

    result = service.list_purchase_orders(
        PaginationParams(),
        PurchaseOrderFilterParams(delivery_date_to=today + timedelta(days=10)),
    )
    ids = {item.id for item in result.items}
    assert near.id in ids
    assert far.id not in ids


# SEARCH


def test_search_matches_reference(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    target = _po(db, vendor, number=f"PO-{today}-001", reference="PO-000042")
    _po(db, vendor, number=f"PO-{today}-002", reference="PO-000099")

    result = service.list_purchase_orders(
        PaginationParams(), PurchaseOrderFilterParams(search="000042")
    )
    ids = {item.id for item in result.items}
    assert ids == {target.id}


def test_search_matches_vendor_name(db):
    service = PurchaseOrderService(db)
    today = date.today().strftime("%Y%m%d")
    acme = _vendor(db, name="Acme Industrial", email="acme@vendor.test")
    other = _vendor(db, name="Globex", email="globex@vendor.test")
    target = _po(db, acme, number=f"PO-{today}-001", reference="PO-000001")
    _po(db, other, number=f"PO-{today}-002", reference="PO-000002")

    result = service.list_purchase_orders(
        PaginationParams(), PurchaseOrderFilterParams(search="Acme")
    )
    ids = {item.id for item in result.items}
    assert ids == {target.id}


# PAGINATION


def test_list_paginates_with_has_next_without_count(db):
    """Default path over-fetches per_page + 1: has_next is exact, total is None."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    for i in range(1, 4):
        _po(db, vendor, number=f"PO-{today}-00{i}", reference=f"PO-00000{i}")

    result = service.list_purchase_orders(
        PaginationParams(page=1, per_page=2), PurchaseOrderFilterParams()
    )
    assert len(result.items) == 2
    assert result.metadata.has_next is True
    assert result.metadata.total is None  # no COUNT unless withTotal
    assert result.metadata.total_pages is None


def test_list_with_total_runs_count(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    for i in range(1, 4):
        _po(db, vendor, number=f"PO-{today}-00{i}", reference=f"PO-00000{i}")

    result = service.list_purchase_orders(
        PaginationParams(page=1, per_page=2, with_total=True),
        PurchaseOrderFilterParams(),
    )
    assert result.metadata.total == 3
    assert result.metadata.total_pages == 2


# N+1 / PROJECTION


def test_list_query_count_is_constant_regardless_of_rows(db):
    """Summary projection + single join: statement count does not grow with
    the number of rows or line items (no N+1)."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")

    _po(db, vendor, number=f"PO-{today}-001", reference="PO-000001")
    with _QueryCounter(db) as counter:
        service.list_purchase_orders(
            PaginationParams(per_page=50), PurchaseOrderFilterParams()
        )
    one_row_queries = counter.count

    for i in range(2, 7):
        _po(db, vendor, number=f"PO-{today}-00{i}", reference=f"PO-00000{i}")
    with _QueryCounter(db) as counter:
        service.list_purchase_orders(
            PaginationParams(per_page=50), PurchaseOrderFilterParams()
        )
    many_rows_queries = counter.count

    assert many_rows_queries == one_row_queries


def test_summary_excludes_line_items(db):
    """The list row is a projection — it carries no line-item collection."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    _po(db, vendor, number=f"PO-{today}-001", reference="PO-000001")

    result = service.list_purchase_orders(
        PaginationParams(), PurchaseOrderFilterParams()
    )
    assert len(result.items) == 1
    assert not hasattr(result.items[0], "line_items")
    assert result.items[0].vendor_name == vendor.vendor_name


# STATUS COUNTS


def test_status_counts_excludes_canceled_from_all(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    _seed_one_per_status(db, vendor)

    counts = service.get_status_counts()
    assert counts.draft == 1
    assert counts.sent == 1
    assert counts.billed == 1
    assert counts.canceled == 1
    assert counts.paid == 0  # no PAID POs seeded
    assert counts.all == 3  # canceled excluded from the All tab


def test_status_counts_includes_paid_bucket(db):
    """PAID POs appear in both the ``paid`` bucket and the ``all`` total.

    Finding 2: PurchaseOrderStatusCounts was missing a ``paid`` field, so
    PAID POs were counted in ``all`` but appeared in no named bucket.
    """
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    _po(
        db,
        vendor,
        number=f"PO-{today}-010",
        reference="PO-000010",
        status=PurchaseOrderStatus.DRAFT,
    )
    _po(
        db,
        vendor,
        number=f"PO-{today}-011",
        reference="PO-000011",
        status=PurchaseOrderStatus.PAID,
    )
    _po(
        db,
        vendor,
        number=f"PO-{today}-012",
        reference="PO-000012",
        status=PurchaseOrderStatus.PAID,
    )

    counts = service.get_status_counts()
    assert counts.paid == 2, "PAID POs must appear in the paid bucket"
    assert counts.draft == 1
    assert counts.all == 3, "PAID POs must be included in the all total"


def test_status_counts_uses_single_query(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    _seed_one_per_status(db, vendor)

    with _QueryCounter(db) as counter:
        service.get_status_counts()
    assert counter.count == 1  # single grouped query, no N+1


# EXPORT


def test_export_loads_line_items(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    _po(db, vendor, number=f"PO-{today}-001", reference="PO-000001")
    _po(db, vendor, number=f"PO-{today}-002", reference="PO-000002")

    rows = service.list_for_export(PurchaseOrderFilterParams(), include_line_items=True)
    assert len(rows) == 2
    for row in rows:
        assert isinstance(row, PurchaseOrder)
        assert len(row.line_items) == 1
        assert row.vendor is not None


def test_export_respects_status_filter(db):
    """Export shares the list filter — the same filtered set, never drift."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    seeded = _seed_one_per_status(db, vendor)

    rows = service.list_for_export(
        PurchaseOrderFilterParams(status=PurchaseOrderStatus.SENT)
    )
    ids = {r.id for r in rows}
    assert ids == {seeded["sent"].id}


def test_export_honours_limit(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    for i in range(1, 4):
        _po(
            db,
            vendor,
            number=f"PO-{today}-00{i}",
            reference=f"PO-00000{i}",
            with_line=False,
        )

    rows = service.list_for_export(PurchaseOrderFilterParams(), limit=2)
    assert len(rows) == 2


def test_export_workbook_renders_filtered_rows(db):
    """The Excel exporter accepts the export rows and produces a workbook."""
    from app.common.excel import ExcelExporter

    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    today = date.today().strftime("%Y%m%d")
    _po(db, vendor, number=f"PO-{today}-001", reference="PO-000001")

    rows = service.list_for_export(PurchaseOrderFilterParams(), include_line_items=True)
    xlsx = ExcelExporter().export_purchase_orders(rows, include_line_items=True)
    assert isinstance(xlsx, bytes)
    # XLSX is a zip archive — starts with the PK signature.
    assert xlsx[:2] == b"PK"
