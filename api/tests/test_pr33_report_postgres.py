"""PostgreSQL integration coverage for the PR #33 report snapshot contract."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import app.modules.reports.audit as report_audit
from app.common.audit import AuditEvent
from app.common.dependencies import get_reports_service
from app.constants.enums import (
    Currency,
    CustomerStatus,
    InvoiceStatus,
    TaxType,
    UserRole,
)
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.statements.schemas import RangePreset, ResolvedPeriod
from tests.conftest import DATABASE_URL, USING_POSTGRES, TestingSessionLocal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not USING_POSTGRES,
        reason="PostgreSQL isolation and pool behavior require PostgreSQL",
    ),
]

PERIOD = ResolvedPeriod.resolve(
    RangePreset.CUSTOM,
    date(2026, 1, 1),
    date(2026, 1, 31),
)


def _insert_invoice(session, customer_id, suffix: str) -> None:
    invoice = Invoice(
        invoice_number=f"INV-SNAPSHOT-{suffix}",
        invoice_reference=f"IN-SNAPSHOT-{suffix}",
        customer_id=customer_id,
        transaction_date=date(2026, 1, 15),
        due_date=date(2026, 2, 15),
        status=InvoiceStatus.SENT,
        currency=Currency.KES,
        subtotal=Decimal("100.00"),
        vat_enabled=False,
        vat_rate=None,
        tax_total=Decimal("0.00"),
        total_due=Decimal("100.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("100.00"),
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLineItem(
            invoice_id=invoice.id,
            line_number=1,
            item_name="Snapshot service",
            description="Snapshot service",
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
            tax_type=TaxType.NO_TAX,
            tax_amount=Decimal("0.00"),
        )
    )
    session.commit()


def test_count_and_export_iterator_share_repeatable_read_snapshot(db):
    customer = Customer(
        customer_type="business",
        company_name="Snapshot Customer",
        first_name="Ada",
        last_name="Snapshot",
        email="snapshot@example.test",
        phone="+254700000001",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(customer)
    db.commit()
    _insert_invoice(db, customer.id, "INITIAL")

    report_session = TestingSessionLocal()
    writer_session = TestingSessionLocal()
    try:
        service = get_reports_service(report_session, SimpleNamespace())
        assert service.get_sales_ledger_total(PERIOD, Currency.KES) == 1

        _insert_invoice(writer_session, customer.id, "CONCURRENT")

        rows = list(
            service.export_sales_ledger(
                PERIOD,
                Currency.KES,
                batch_size=100,
            )
        )
        assert [row.number for row in rows] == ["INV-SNAPSHOT-INITIAL"]

        report_session.commit()
        actor_id = uuid4()
        report_audit._append_audit(
            report_session,
            actor_id=actor_id,
            report_type="sales",
            action="report_export",
            payload={"success": True},
        )
        report_session.commit()

        stored = (
            report_session.query(AuditEvent)
            .filter(
                AuditEvent.actor_id == actor_id,
                AuditEvent.action == "report_export",
            )
            .one()
        )
        assert stored.after == {"success": True}
    finally:
        report_session.close()
        writer_session.close()


def test_same_session_audit_works_with_single_connection_pool():
    engine = create_engine(
        str(DATABASE_URL),
        future=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        session.execute(text("SELECT 1"))
        get_reports_service(session, SimpleNamespace())

        actor = SimpleNamespace(id=uuid4(), role=UserRole.ADMIN)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/reports/sales",
                "query_string": b"range=this_month&currency=KES",
                "headers": [],
            }
        )
        dependency = report_audit.authorize_and_audit_report_access(
            request,
            session,
            actor,
        )
        next(dependency)
        with pytest.raises(StopIteration):
            next(dependency)

        assert (
            session.query(AuditEvent).filter(AuditEvent.actor_id == actor.id).count()
            == 1
        )
    finally:
        session.close()
        engine.dispose()
