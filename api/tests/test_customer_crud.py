"""Customer CRUD round-trip backfill (service layer)."""

import pytest

from app.common.exceptions import ConflictException, NotFoundException
from app.common.pagination import PaginationParams
from app.constants.enums import CustomerStatus, CustomerType
from app.modules.customers.schemas import CustomerCreate, CustomerUpdate
from app.modules.customers.service import CustomerService


def _create_payload(**overrides) -> CustomerCreate:
    base = dict(
        customer_type=CustomerType.BUSINESS,
        company_name="Acme Ltd",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@acme.com",
        phone="0712345678",
        address="123 Industrial Area",
        country="KE",
        province="Nairobi",
        city="Nairobi",
        postal_code="00100",
    )
    base.update(overrides)
    return CustomerCreate(**base)


def test_create_then_get_roundtrip(db):
    svc = CustomerService(db)
    created = svc.create(_create_payload())
    assert created.id is not None
    assert created.email == "ada@acme.com"

    fetched = svc.get_by_id(created.id)
    assert fetched.id == created.id
    assert fetched.status == CustomerStatus.ACTIVE


def test_create_normalises_email_lowercase(db):
    svc = CustomerService(db)
    created = svc.create(_create_payload(email="MixedCase@Acme.Com"))
    assert created.email == "mixedcase@acme.com"


def test_duplicate_email_rejected_case_insensitive(db):
    svc = CustomerService(db)
    svc.create(_create_payload(email="dup@acme.com"))
    with pytest.raises(ConflictException):
        svc.create(_create_payload(email="DUP@acme.com"))


def test_list_excludes_soft_deleted_by_default(db):
    svc = CustomerService(db)
    keep = svc.create(_create_payload(email="keep@acme.com"))
    gone = svc.create(_create_payload(email="gone@acme.com"))
    svc.delete(gone.id)  # soft delete

    result = svc.list_customers(PaginationParams(page=1, per_page=50))
    ids = {item.id for item in result.items}
    assert keep.id in ids
    assert gone.id not in ids


def test_get_soft_deleted_raises_by_default(db):
    svc = CustomerService(db)
    c = svc.create(_create_payload(email="sd@acme.com"))
    svc.delete(c.id)
    with pytest.raises(NotFoundException):
        svc.get_by_id(c.id)


def test_update_changes_persisted_fields(db):
    svc = CustomerService(db)
    c = svc.create(_create_payload(email="upd@acme.com"))
    updated = svc.update(c.id, CustomerUpdate(first_name="Grace"))
    assert updated.first_name == "Grace"
    assert svc.get_by_id(c.id).first_name == "Grace"


def test_update_cannot_set_status_directly(db):
    """Sstatus is not a CustomerUpdate field (bypass guard)."""
    assert "status" not in CustomerUpdate.model_fields
