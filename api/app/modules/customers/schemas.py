from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.common.validators import (
    capitalize_location as _capitalize_location,
)
from app.common.validators import (
    empty_str_to_none as _empty_str_to_none,
)
from app.common.validators import (
    normalize_phone,
    validate_country_code,
)
from app.constants.enums import (
    BillingCurrency,
    BillingPaymentTerms,
    Currency,
    CustomerType,
    Industry,
    TaxTreatment,
)

# Request Schemas


class CustomerCreate(BaseModel):
    """Request body for creating a new customer."""

    customer_type: CustomerType = Field(
        ..., description="Customer type", alias="customerType"
    )
    company_name: str | None = Field(
        None,
        max_length=255,
        description="Company name (required for business customers)",
        alias="companyName",
    )
    first_name: str | None = Field(
        None,
        max_length=100,
        description="First name (required for individual customers)",
        alias="firstName",
    )
    last_name: str | None = Field(
        None,
        max_length=100,
        description="Last name (required for individual customers)",
        alias="lastName",
    )
    email: EmailStr | None = Field(None, description="Email address")
    phone: str | None = Field(None, description="Phone number in international format")
    website: str | None = Field(None, max_length=500, description="Website URL")
    vat_number: str | None = Field(
        None, max_length=50, description="VAT/Tax number", alias="vatNumber"
    )
    currency: Currency = Field(default=Currency.KES, description="Default currency")
    industry: Industry | None = Field(None, description="Industry classification")
    tenant_domain: str | None = Field(
        None,
        max_length=255,
        description="Microsoft 365 tenant domain",
        alias="tenantDomain",
    )
    owner_id: UUID | None = Field(
        None, description="Account owner (sales rep) user ID", alias="ownerId"
    )
    primary_currency: BillingCurrency | None = Field(
        None,
        description="Which billing profile (USD|KES) is the default",
        alias="primaryCurrency",
    )
    address: str | None = Field(None, min_length=5, description="Primary address")
    address2: str | None = Field(None, description="Secondary address")
    country: str | None = Field(
        None, min_length=2, max_length=2, description="ISO country code"
    )
    city: str | None = Field(None, min_length=2, max_length=100, description="City")
    postal_code: str | None = Field(
        None, min_length=3, max_length=20, description="Postal code", alias="postalCode"
    )

    @field_validator(
        "company_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "website",
        "vat_number",
        "tenant_domain",
        "address",
        "address2",
        "city",
        "postal_code",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None for optional fields."""
        return _empty_str_to_none(v)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None, info) -> str | None:
        """Validate and normalize phone number to E.164 format."""
        if v is None:
            return None
        country = info.data.get("country")
        return normalize_phone(v, country)

    @field_validator("country")
    @classmethod
    def validate_country(cls, v: str | None) -> str | None:
        """Validate and normalize ISO 3166-1 alpha-2 country code."""
        return validate_country_code(v)

    @field_validator("city")
    @classmethod
    def capitalize_location(cls, v: str | None) -> str | None:
        """Capitalize location names for consistency."""
        return _capitalize_location(v)

    @model_validator(mode="after")
    def validate_business_company_name(self) -> "CustomerCreate":
        """Ensure business customers have a company name."""
        if self.customer_type == CustomerType.BUSINESS and not self.company_name:
            raise ValueError("company_name is required for business customers")
        return self

    @model_validator(mode="after")
    def validate_individual_phone(self) -> "CustomerCreate":
        """Individual customers must have a phone number.

        Phone is optional for companies, which are typically reached through
        a billing contact, but an individual is only reachable directly - so
        the field is conditionally required rather than globally optional.
        """
        if self.customer_type == CustomerType.INDIVIDUAL and not self.phone:
            raise ValueError("Phone number is required for individual customers.")
        return self

    @model_validator(mode="after")
    def validate_individual_name(self) -> "CustomerCreate":
        """Individual customers must have a first and last name.

        Names are optional for companies, which are identified by
        company_name and may have no billing contact on file yet, but an
        individual customer has no other identity to fall back on.
        """
        if self.customer_type == CustomerType.INDIVIDUAL and (
            not self.first_name or not self.last_name
        ):
            raise ValueError(
                "First name and last name are required for individual customers."
            )
        return self

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "customerType": "individual",
                    "firstName": "John",
                    "lastName": "Doe",
                    "email": "john.doe@example.com",
                    "phone": "0712345678",
                    "currency": "KES",
                    "address": "123 Main Street",
                    "city": "Nairobi",
                    "country": "KE",
                    "postalCode": "00100",
                },
                {
                    "customer_type": "business",
                    "company_name": "Acme Corporation",
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "email": "jane@acme.com",
                    "phone": "+254700000000",
                    "website": "https://acme.com",
                    "vat_number": "KE1234567890",
                    "currency": "KES",
                    "address": "456 Business Ave",
                    "country": "KE",
                    "city": "Nairobi",
                    "postal_code": "00100",
                },
            ]
        },
    }


class CustomerUpdate(BaseModel):
    """Request body for updating a customer. All fields optional."""

    customer_type: CustomerType | None = Field(None, alias="customerType")
    company_name: str | None = Field(None, max_length=255, alias="companyName")
    first_name: str | None = Field(
        None, min_length=1, max_length=100, alias="firstName"
    )
    last_name: str | None = Field(None, min_length=1, max_length=100, alias="lastName")
    email: EmailStr | None = None
    phone: str | None = None
    website: str | None = Field(None, max_length=500)
    vat_number: str | None = Field(None, max_length=50, alias="vatNumber")
    currency: Currency | None = None
    industry: Industry | None = None
    tenant_domain: str | None = Field(None, max_length=255, alias="tenantDomain")
    owner_id: UUID | None = Field(None, alias="ownerId")
    primary_currency: BillingCurrency | None = Field(None, alias="primaryCurrency")
    address: str | None = None
    address2: str | None = None
    country: str | None = Field(None, min_length=2, max_length=2)
    city: str | None = None
    postal_code: str | None = Field(None, alias="postalCode")
    # `status` is intentionally NOT editable here: lifecycle changes must
    # go through the dedicated activate/deactivate endpoints so the state
    # machine is enforced. A plain PUT must not be able to deactivate/delete.

    @field_validator(
        "company_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "website",
        "vat_number",
        "tenant_domain",
        "address",
        "address2",
        "city",
        "postal_code",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        return _empty_str_to_none(v)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None, info) -> str | None:
        """Validate and normalize phone number to E.164 format."""
        if v is None:
            return v
        country = info.data.get("country")
        return normalize_phone(v, country)

    @field_validator("country")
    @classmethod
    def validate_country(cls, v: str | None) -> str | None:
        """Validate ISO country code if provided."""
        return validate_country_code(v)

    @field_validator("city")
    @classmethod
    def capitalize_location(cls, v: str | None) -> str | None:
        """Capitalize location names."""
        return _capitalize_location(v)

    model_config = {
        "populate_by_name": True,
    }


class BillingProfileUpdate(BaseModel):
    """PATCH body for a customer billing profile. All fields optional.

    Any accepted change flips the profile back to unsynced server-side;
    ``code`` and ``currency`` are immutable once issued (the code is the
    accounting identity of the profile).
    """

    payment_terms: BillingPaymentTerms | None = Field(
        None, description="Payment terms", alias="paymentTerms"
    )
    tax_treatment: TaxTreatment | None = Field(
        None, description="Tax treatment", alias="taxTreatment"
    )
    credit_limit: Decimal | None = Field(
        None,
        ge=0,
        description="Credit limit in the profile's own currency",
        alias="creditLimit",
    )

    model_config = {
        "populate_by_name": True,
    }


# Response Schemas


class BillingProfileResponse(BaseModel):
    """One per-currency billing profile of a customer."""

    id: UUID
    customer_id: UUID
    currency: str
    code: str
    payment_terms: str
    tax_treatment: str
    credit_limit: Decimal
    synced: bool
    synced_at: datetime | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CustomerResponse(BaseModel):
    """Full customer record returned in API responses."""

    id: UUID
    customer_type: str
    status: str
    company_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    vat_number: str | None = None
    currency: str
    balance: Decimal
    industry: str | None = None
    tenant_domain: str | None = None
    owner_id: UUID | None = None
    primary_currency: str | None = None
    billing_profiles: list[BillingProfileResponse] = Field(default_factory=list)
    address: str | None = None
    address2: str | None = None
    country: str | None = None
    city: str | None = None
    postal_code: str | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CustomerSummary(BaseModel):
    """Lightweight customer summary for lists."""

    id: UUID
    customer_type: str
    status: str
    display_name: str
    # Nullable since the contact fields became optional (e2f3a4b5c6d7): these
    # are read straight off the ORM row, so a plain `str` makes the whole
    # list endpoint 500 the moment one customer has no email or phone.
    email: str | None = None
    phone: str | None = None
    balance: Decimal
    currency: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CustomerStatusCounts(BaseModel):
    """Customer counts by status for dashboard filter tabs."""

    all: int = 0
    active: int = 0
    inactive: int = 0
    suspended: int = 0
    deleted: int = 0


class FinancialSummary(BaseModel):
    """Financial summary for customer overview."""

    total_unpaid: Decimal = Field(
        default=Decimal("0.00"), description="Total unpaid invoice balance"
    )
    overdue: Decimal = Field(
        default=Decimal("0.00"), description="Total overdue invoice balance"
    )
    total_invoiced: Decimal = Field(
        default=Decimal("0.00"), description="Total invoiced amount (all time)"
    )
    total_paid: Decimal = Field(
        default=Decimal("0.00"), description="Total paid amount (all time)"
    )


class CustomerDetailResponse(BaseModel):
    """Single customer with invoices and statements."""

    customer: CustomerResponse
    financial_summary: FinancialSummary
    recent_invoices: list[dict] = Field(
        default_factory=list, description="Recent 5 invoices"
    )
    statement: "CustomerStatement | None" = Field(
        default=None, description="Current period statement"
    )


class StatementTransaction(BaseModel):
    """Single transaction line in a statement."""

    date: date
    description: str
    amount: Decimal
    payment: Decimal
    balance: Decimal


class StatementSummary(BaseModel):
    """Account summary for statement header."""

    opening_balance: Decimal
    invoiced_amount: Decimal
    amount_paid: Decimal
    balance_due: Decimal


class CustomerDeleteCheckResponse(BaseModel):
    """Pre-delete validation response."""

    can_delete: bool = Field(description="Whether soft delete is allowed")
    can_hard_delete: bool = Field(description="Whether hard delete is allowed")
    delete_type: str = Field(
        description="Recommended delete type: 'soft_only' | 'hard_allowed'"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Warnings about deletion"
    )
    associated_records: dict[str, int] = Field(
        default_factory=dict, description="Count of related records by type"
    )
    message: str = Field(description="Human-readable summary")

    model_config = {
        "json_schema_extra": {
            "example": {
                "can_delete": True,
                "can_hard_delete": False,
                "delete_type": "soft_only",
                "warnings": [
                    "Customer has outstanding balance: KES 15000.00",
                    "42 invoice(s) associated with this customer (3 unpaid)",
                ],
                "associated_records": {
                    "invoices": 42,
                    "quotes": 5,
                    "payments": 38,
                },
                "message": "Customer has associated records. Soft delete recommended.",
            }
        }
    }


class CustomerDeleteResponse(BaseModel):
    """Response after deleting a customer."""

    customer_id: UUID = Field(description="ID of the deleted customer")
    delete_type: str = Field(description="Type of deletion performed: 'soft' | 'hard'")
    deleted_at: datetime = Field(description="Timestamp when deletion occurred")
    warnings: list[str] = Field(
        default_factory=list, description="Any warnings from the delete operation"
    )
    associated_records: dict[str, int] = Field(
        default_factory=dict,
        description="Count of associated records that were affected",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "customer_id": "123e4567-e89b-12d3-a456-426614174000",
                "delete_type": "soft",
                "deleted_at": "2024-05-14T13:45:00Z",
                "warnings": [],
                "associated_records": {},
            }
        }
    }


class CustomerStatement(BaseModel):
    """Complete customer statement."""

    customer: CustomerResponse
    period_start: date
    period_end: date
    summary: StatementSummary
    transactions: list[StatementTransaction]
    generated_at: datetime
