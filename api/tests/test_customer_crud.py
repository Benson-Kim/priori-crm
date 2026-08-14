"""Customer CRUD round-trip backfill (service layer)."""

import pytest
from pydantic import ValidationError

from app.common.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
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
    updated = svc.update(
        c.id, CustomerUpdate(first_name="Grace"), expected_version=c.version
    )
    assert updated.first_name == "Grace"
    assert svc.get_by_id(c.id).first_name == "Grace"


def test_update_cannot_set_status_directly(db):
    """Sstatus is not a CustomerUpdate field (bypass guard)."""
    assert "status" not in CustomerUpdate.model_fields


# Optional contact fields
#
# postal code, website, email and phone are all optional. email additionally
# keeps its unique constraint, so the absent case has to coexist with dedup.


def test_create_without_any_optional_contact_fields(db):
    """A customer can be recorded with no email, phone, postal code or site."""
    svc = CustomerService(db)
    created = svc.create(
        _create_payload(
            email=None,
            phone=None,
            postal_code=None,
            website=None,
        )
    )

    assert created.id is not None
    assert created.email is None
    assert created.phone is None
    assert created.postal_code is None
    assert created.website is None
    assert svc.get_by_id(created.id).email is None


def test_empty_strings_are_stored_as_absent(db):
    """A form submitting "" for an optional field records no value."""
    svc = CustomerService(db)
    created = svc.create(
        _create_payload(email="", phone="", postal_code="", website="")
    )

    assert created.email is None
    assert created.phone is None
    assert created.postal_code is None
    assert created.website is None


def test_multiple_customers_may_omit_email(db):
    """The unique constraint must not collapse rows that have no email.

    Postgres treats NULLs as distinct, and the service skips the dedup
    check when no address is supplied.
    """
    svc = CustomerService(db)
    first = svc.create(_create_payload(email=None))
    second = svc.create(_create_payload(email=None))

    assert first.id != second.id


def test_duplicate_email_still_rejected_when_supplied(db):
    """Relaxing the field must not weaken dedup for supplied addresses."""
    svc = CustomerService(db)
    svc.create(_create_payload(email="still-unique@acme.com"))

    with pytest.raises(ConflictException):
        svc.create(_create_payload(email="STILL-UNIQUE@acme.com"))


def test_update_can_clear_email(db):
    """Clearing an address is allowed and does not report a false conflict."""
    svc = CustomerService(db)
    other = svc.create(_create_payload(email=None))
    c = svc.create(_create_payload(email="clearme@acme.com"))

    updated = svc.update(c.id, CustomerUpdate(email=None), expected_version=c.version)

    assert updated.email is None
    assert svc.get_by_id(other.id).email is None


def test_formatted_address_omits_absent_parts(db):
    """The display address skips fields the customer does not have."""
    svc = CustomerService(db)
    created = svc.create(_create_payload(email=None, postal_code=None))

    formatted = svc.get_by_id(created.id).formatted_address

    assert "123 Industrial Area" in formatted
    assert "Nairobi" in formatted
    assert formatted.endswith("KE")
    assert ", ," not in formatted


# Phone is conditionally required
#
# Individual customers must have a phone number; companies may omit it. The
# create rule lives in CustomerCreate, but the update rule needs the service:
# CustomerUpdate cannot see the stored type, and either half of the
# (type, phone) pair may be absent from a payload.


def _individual_payload(**overrides) -> CustomerCreate:
    base = dict(
        customer_type=CustomerType.INDIVIDUAL,
        first_name="Ada",
        last_name="Lovelace",
        email=None,
        phone="0712345678",
        address="123 Industrial Area",
        country="KE",
        city="Nairobi",
    )
    base.update(overrides)
    return CustomerCreate(**base)


def test_create_individual_without_phone_rejected(db):
    with pytest.raises(ValidationError) as exc:
        _individual_payload(phone=None)

    assert "Phone number is required for individual customers." in str(exc.value)


def test_create_individual_with_empty_phone_rejected(db):
    """ "" is coerced to None first, so it must fail the same way."""
    with pytest.raises(ValidationError):
        _individual_payload(phone="")


def test_create_company_without_phone_allowed(db):
    svc = CustomerService(db)
    created = svc.create(_create_payload(phone=None))

    assert created.phone is None
    assert created.customer_type == CustomerType.BUSINESS


def test_update_cannot_clear_individual_phone(db):
    svc = CustomerService(db)
    c = svc.create(_individual_payload())

    with pytest.raises(ValidationException):
        svc.update(c.id, CustomerUpdate(phone=None), expected_version=c.version)

    assert svc.get_by_id(c.id).phone is not None


def test_update_can_clear_company_phone(db):
    svc = CustomerService(db)
    c = svc.create(_create_payload(email="clearphone@acme.com"))

    updated = svc.update(c.id, CustomerUpdate(phone=None), expected_version=c.version)

    assert updated.phone is None


def test_switching_company_without_phone_to_individual_rejected(db):
    """The rule is evaluated against the state the customer would end up in."""
    svc = CustomerService(db)
    c = svc.create(_create_payload(email="notype@acme.com", phone=None))

    with pytest.raises(ValidationException):
        svc.update(
            c.id,
            CustomerUpdate(customer_type=CustomerType.INDIVIDUAL),
            expected_version=c.version,
        )


def test_switching_company_to_individual_with_phone_allowed(db):
    svc = CustomerService(db)
    c = svc.create(_create_payload(email="withphone@acme.com", phone="0712345678"))

    updated = svc.update(
        c.id,
        CustomerUpdate(customer_type=CustomerType.INDIVIDUAL),
        expected_version=c.version,
    )

    assert updated.customer_type == CustomerType.INDIVIDUAL
