"""Route-level regressions for report export file lifecycle."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import load_workbook

import app.modules.reports.audit as report_audit
import app.modules.reports.router as report_router
from app.common.audit import AuditEvent
from app.common.dependencies import get_current_user, get_reports_service
from app.common.exceptions import BadRequestException
from app.constants.enums import UserRole
from app.lib.config import settings
from app.main import app
from app.modules.reports.schemas import SalesLedgerEntry
from app.modules.reports.service import ReportsService


def _sales_row(index: int) -> SalesLedgerEntry:
    return SalesLedgerEntry(
        id=uuid4(),
        customer_name=f"Customer {index}",
        reference=f"REF-{index}",
        number=f"INV-{index}",
        date=date(2026, 1, index),
        status="sent",
        currency="KES",
        subtotal=Decimal("100.00"),
        discount=Decimal("0.00"),
        net_revenue=Decimal("100.00"),
        amount=Decimal("116.00"),
        tax=Decimal("16.00"),
        balance_due=Decimal("116.00"),
    )


class _SalesService:
    validate_export_size = staticmethod(ReportsService.validate_export_size)

    def __init__(self, rows: list[SalesLedgerEntry]) -> None:
        self.rows = rows

    def get_sales_ledger_total(self, *args, **kwargs) -> int:
        return len(self.rows)

    def export_sales_ledger(self, *args, **kwargs):
        yield from self.rows


def _install_overrides(service: _SalesService) -> None:
    actor = SimpleNamespace(id=uuid4(), role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_reports_service] = lambda: service


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_reports_service, None)


def _params() -> dict[str, str]:
    return {
        "range": "custom",
        "dateFrom": "2026-01-01",
        "dateTo": "2026-01-31",
        "currency": "KES",
    }


def test_tax_export_has_a_lower_operational_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "TAX_REPORT_EXPORT_MAX_ROWS", 2)

    ReportsService.validate_export_size(
        2,
        "VAT reconciliation",
        max_rows=settings.TAX_REPORT_EXPORT_MAX_ROWS,
    )
    with pytest.raises(BadRequestException) as exc_info:
        ReportsService.validate_export_size(
            3,
            "VAT reconciliation",
            max_rows=settings.TAX_REPORT_EXPORT_MAX_ROWS,
        )
    assert "more than 2 rows" in str(exc_info.value)


def test_successful_export_returns_workbook_and_deletes_file(
    client,
    db,
    monkeypatch,
    tmp_path: Path,
):
    artifact = tmp_path / "sales.xlsx"
    monkeypatch.setattr(report_router, "_temporary_xlsx_path", lambda: str(artifact))
    _install_overrides(_SalesService([_sales_row(1), _sales_row(2)]))
    try:
        response = client.get("/api/v1/reports/sales/export", params=_params())
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert not artifact.exists()

    workbook = load_workbook(
        BytesIO(response.content),
        read_only=True,
        data_only=False,
    )
    assert list(workbook["Sales"].values)[1][2] == "INV-1"

    event = db.query(AuditEvent).filter(AuditEvent.action == "report_export").one()
    assert event.after["export_metrics"] == {"exported_data_row_count": 2}


def test_export_generation_failure_removes_partial_file(
    client,
    monkeypatch,
    tmp_path: Path,
):
    artifact = tmp_path / "failed.xlsx"
    monkeypatch.setattr(report_router, "_temporary_xlsx_path", lambda: str(artifact))

    def fail_export(*args, destination=None, **kwargs):
        assert destination is not None
        Path(destination).write_bytes(b"partial")
        raise RuntimeError("boom")

    monkeypatch.setattr(report_router._exporter, "export_sales_report", fail_export)
    _install_overrides(_SalesService([_sales_row(1)]))
    try:
        with pytest.raises(RuntimeError, match="boom"):
            client.get("/api/v1/reports/sales/export", params=_params())
    finally:
        _clear_overrides()

    assert not artifact.exists()


def test_audit_failure_blocks_response_and_removes_file(
    client,
    monkeypatch,
    tmp_path: Path,
):
    artifact = tmp_path / "audit-failed.xlsx"
    monkeypatch.setattr(report_router, "_temporary_xlsx_path", lambda: str(artifact))

    def fail_audit(*args, **kwargs):
        raise OSError("audit down")

    monkeypatch.setattr(report_audit, "_append_audit", fail_audit)
    _install_overrides(_SalesService([_sales_row(1)]))
    try:
        with pytest.raises(OSError, match="audit down"):
            client.get("/api/v1/reports/sales/export", params=_params())
    finally:
        _clear_overrides()

    assert not artifact.exists()
