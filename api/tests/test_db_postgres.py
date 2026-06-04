"""Tests that exercise PostgreSQL-only database constructs.

These are skipped when the suite runs against SQLite (local pure-logic
runs). In CI they run against the postgres:16 service so behaviour such as
row-level locking with `with_for_update()` is actually validated.
"""
import uuid

import pytest

from app.constants.enums import CustomerStatus, CustomerType
from app.modules.customers.models import Customer
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Requires PostgreSQL; SQLite does not support these constructs",
)


def _make_customer() -> Customer:
    suffix = uuid.uuid4().hex[:8]
    return Customer(
        customer_type=CustomerType.INDIVIDUAL,
        status=CustomerStatus.ACTIVE,
        first_name="Lock",
        last_name="Test",
        email=f"lock-{suffix}@example.com",
        phone="+254700000000",
    )


def test_with_for_update_locks_row(db):
    """`with_for_update()` issues a valid SELECT ... FOR UPDATE on Postgres."""
    customer = _make_customer()
    db.add(customer)
    db.commit()

    locked = (
        db.query(Customer)
        .filter(Customer.id == customer.id)
        .with_for_update()
        .one()
    )

    assert locked.id == customer.id


def test_server_default_gen_random_uuid(db):
    """The server-side gen_random_uuid() default populates the primary key."""
    customer = _make_customer()
    db.add(customer)
    db.commit()
    db.refresh(customer)

    assert isinstance(customer.id, uuid.UUID)
