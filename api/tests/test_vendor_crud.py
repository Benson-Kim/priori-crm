"""vendor CRUD + lifecycle backfill (service layer)."""

import uuid

import pytest

from app.common.exceptions import NotFoundException
from app.common.pagination import PaginationParams
from app.constants.enums import VendorStatus
from app.modules.vendors.schemas import VendorCreate, VendorUpdate
from app.modules.vendors.service import VendorService


def _create_payload(**overrides) -> VendorCreate:
    base = dict(
        vendor_name="Priori Studio Ltd",
        email="accounts@studio.com",
        phone_primary="0700000000",
        currency="KES",
    )
    base.update(overrides)
    return VendorCreate(**base)


def test_create_then_get_roundtrip(db):
    svc = VendorService(db)
    created = svc.create(_create_payload())
    assert created.id is not None
    assert created.status == VendorStatus.ACTIVE

    fetched = svc.get_by_id(created.id)
    assert fetched.id == created.id
    assert fetched.vendor_name == "Priori Studio Ltd"


def test_get_unknown_vendor_raises(db):
    svc = VendorService(db)
    with pytest.raises(NotFoundException):
        svc.get_by_id(uuid.uuid4())


def test_list_returns_created_vendor(db):
    svc = VendorService(db)
    created = svc.create(_create_payload(email="list@studio.com"))
    result = svc.list_vendors(PaginationParams(page=1, per_page=50))
    assert any(item.id == created.id for item in result.items)


def test_update_changes_persisted_fields(db):
    svc = VendorService(db)
    created = svc.create(_create_payload(email="upd@studio.com"))
    updated = svc.update(
        created.id,
        VendorUpdate(vendor_name="Renamed Studio"),
        expected_version=created.version,
    )
    assert updated.vendor_name == "Renamed Studio"


def test_update_cannot_set_status_directly(db):
    """Status is not a VendorUpdate field (state-machine bypass guard)."""
    assert "status" not in VendorUpdate.model_fields


def test_lifecycle_transitions_via_dedicated_methods(db):
    svc = VendorService(db)
    created = svc.create(_create_payload(email="life@studio.com"))

    deactivated = svc.deactivate(created.id)
    assert deactivated.status == VendorStatus.INACTIVE

    reactivated = svc.activate(created.id)
    assert reactivated.status == VendorStatus.ACTIVE
