"""Tests for the vendor detail path (VEND-BE-2).

VendorService.get_detail() and get_transactions_count() were dead, unrouted
code that called a non-existent self._build_vendor_response(...), guaranteeing
an AttributeError if ever invoked. They have been removed. The routed
GET /vendors/{id} endpoint uses get_by_id() -> VendorResponse instead.

These tests lock in that the dead method is gone and that get_by_id returns a
vendor with computed payables attached for serialization.
"""
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

from app.constants.enums import VendorStatus
from app.modules.vendors.service import VendorService


class _FakeVendor:
    """Minimal Vendor stand-in for unit tests."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.vendor_name = "Acme Supplies Ltd"
        self.status = VendorStatus.ACTIVE
        self.currency = "KES"


def _service_with(vendor: _FakeVendor) -> VendorService:
    db = MagicMock()
    svc = VendorService(db)
    svc._get_vendor_or_404 = MagicMock(return_value=vendor)  # type: ignore[method-assign]
    # No expense/bill data — payables computation returns safe zeros.
    svc._compute_payables_for_vendor = MagicMock(  # type: ignore[method-assign]
        return_value={
            "total_unpaid": Decimal("0.00"),
            "overdue_total": Decimal("0.00"),
        }
    )
    return svc


class TestVendorDetail:
    def test_dead_get_detail_method_removed(self):
        """The unrouted get_detail() must no longer exist (VEND-BE-2)."""
        assert not hasattr(VendorService, "get_detail")
        assert not hasattr(VendorService, "get_transactions_count")

    def test_get_by_id_attaches_payables(self):
        """The routed vendor detail path returns a vendor with payables set."""
        vendor = _FakeVendor()
        svc = _service_with(vendor)

        result = svc.get_by_id(vendor.id)

        assert result is vendor
        assert result.payables == Decimal("0.00")
        assert result.total_unpaid == Decimal("0.00")
        assert result.overdue_total == Decimal("0.00")
