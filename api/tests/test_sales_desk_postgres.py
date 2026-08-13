"""PostgreSQL integration coverage for the Sales Desk snapshot contract.

Issue #45 acceptance: all numbers server-computed and internally consistent
under ONE snapshot. These tests prove the REPEATABLE READ semantics against
a real PostgreSQL (mirroring ``test_pr33_report_postgres``): a request's
service keeps returning the same numbers even while another session commits
new deals mid-request.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.common.dependencies import get_sales_desk_service
from tests.conftest import USING_POSTGRES, TestingSessionLocal
from tests.test_sales_desk import _make_customer, _make_deal, _make_owner

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not USING_POSTGRES,
        reason="PostgreSQL isolation and pool behavior require PostgreSQL",
    ),
]


def test_dashboard_and_export_share_one_repeatable_read_snapshot(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("1000.00"))
    db.commit()

    analytics_session = TestingSessionLocal()
    writer_session = TestingSessionLocal()
    try:
        service = get_sales_desk_service(analytics_session, SimpleNamespace())
        first = service.get_dashboard()
        assert first.kpis.total_arr_pipeline.value == Decimal("1000.00")
        assert first.kpis.pipeline_weighted.open_deal_count == 1

        # Another session commits a new open deal mid-request.
        writer_owner = _make_owner(writer_session, first="Joy", last="Nduta")
        writer_customer = _make_customer(writer_session, name="Concurrent Co")
        _make_deal(
            writer_session, writer_owner, writer_customer, value=Decimal("9000.00")
        )
        writer_session.commit()

        # The service's snapshot must not see the concurrent commit: the
        # dashboard, notifications and overview all stay mutually consistent.
        second = service.get_dashboard()
        assert second == first

        overview = service.get_pipeline_overview()
        assert overview.open_pipeline_total == Decimal("1000.00")
        assert overview.team.open_count == 1

        # A fresh snapshot (new transaction) DOES see the new deal.
        analytics_session.commit()
        fresh = get_sales_desk_service(analytics_session, SimpleNamespace())
        assert fresh.get_dashboard().kpis.total_arr_pipeline.value == Decimal(
            "10000.00"
        )
    finally:
        analytics_session.close()
        writer_session.close()
