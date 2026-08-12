"""Application-wide enumerations for type safety."""

from enum import StrEnum


class UserRole(StrEnum):
    """Authorization roles for application users.

    ADMIN can perform any action. MANAGER may perform destructive and
    financial operations (hard-delete, record-payment, approve, convert).
    MEMBER may perform ordinary create/update operations only.
    """

    ADMIN = "admin"
    MANAGER = "manager"
    MEMBER = "member"

    @property
    def is_privileged(self) -> bool:
        """Whether this role may perform destructive/financial actions."""
        return self in PRIVILEGED_ROLES


#: Single source of truth for the roles allowed to perform destructive /
#: financial actions (hard-delete, record-payment, approve, convert, settle).
#: Use this (or the require_privileged dependency) instead of repeating the
#: (MANAGER, ADMIN) tuple at every call site.
PRIVILEGED_ROLES: frozenset["UserRole"] = frozenset({UserRole.ADMIN, UserRole.MANAGER})


class CustomerStatus(StrEnum):
    """Customer account status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class CustomerType(StrEnum):
    """Customer classification type."""

    INDIVIDUAL = "individual"
    BUSINESS = "business"


class Currency(StrEnum):
    """Supported currencies (ISO 4217)."""

    KES = "KES"  # Kenyan Shilling
    USD = "USD"  # US Dollar
    EUR = "EUR"  # Euro
    GBP = "GBP"  # British Pound


class BillingCurrency(StrEnum):
    """Currencies for which a customer billing profile exists in accounting.

    Deliberately a strict subset of :class:`Currency`: every customer is
    registered once with exactly one USD and one KES billing profile
    (deal-desk parity). EUR/GBP remain display/conversion-only.
    """

    USD = "USD"
    KES = "KES"


class Industry(StrEnum):
    """Customer industry classification (ratified design vocabulary).

    The 12 values ratified from the Sales Desk design exports
    (``docs/sales-desk-designs/`` on branch ``sales-desk-designs``; see the
    review on !38 and issue #53): the deal-desk prototype's original list
    with 'Hospitality' renamed to 'Hospitality & tourism' plus
    'Agriculture & export' and 'Insurance'. Must stay identical to the
    IN-list in migration ``d5e6f7a8b9c0``.
    """

    FINANCIAL_SERVICES = "Financial services"
    HEALTHCARE = "Healthcare"
    EDUCATION = "Education"
    LOGISTICS_TRANSPORT = "Logistics & transport"
    HOSPITALITY_TOURISM = "Hospitality & tourism"
    AGRICULTURE_EXPORT = "Agriculture & export"
    INSURANCE = "Insurance"
    MANUFACTURING = "Manufacturing"
    PROFESSIONAL_SERVICES = "Professional services"
    NGO_NON_PROFIT = "NGO / Non-profit"
    RETAIL = "Retail"
    OTHER = "Other"


class TaxTreatment(StrEnum):
    """Tax treatment of a customer billing profile.

    Display values from the deal-desk prototype; each maps 1:1 onto an
    existing document-level :class:`TaxType` so profile settings can drive
    line-item taxation without a second taxonomy.
    """

    VAT_16 = "VAT 16%"
    ZERO_RATED_EXPORT = "Zero-rated (export)"
    EXEMPT = "Exempt"

    @property
    def tax_type(self) -> "TaxType":
        """The document-level TaxType this treatment corresponds to."""
        return _TAX_TREATMENT_TO_TAX_TYPE[self]


class BillingPaymentTerms(StrEnum):
    """Payment-terms options offered on a customer billing profile."""

    ON_RECEIPT = "On receipt"
    NET_14 = "14 days"
    NET_30 = "30 days"
    NET_45 = "45 days"
    NET_60 = "60 days"


class TransactionType(StrEnum):
    """Transaction classification."""

    SALE = "sale"
    REFUND = "refund"
    PAYMENT = "payment"
    ADJUSTMENT = "adjustment"


class InvoiceStatus(StrEnum):
    """Invoice lifecycle status with strict state machine."""

    DRAFT = "draft"  # Created but not sent
    SENT = "sent"  # Sent to customer, awaiting payment
    PARTIAL = "partial"  # Partially paid
    PAID = "paid"  # Fully paid
    OVERDUE = "overdue"  # Past due date, unpaid
    CANCELED = "canceled"  # Voided/cancelled (immutable)


class PaymentMethod(StrEnum):
    """Payment method types."""

    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CHECK = "check"
    CARD = "card"
    MOBILE_MONEY = "mobile_money"
    OTHER = "other"


class TaxType(StrEnum):
    """Tax types for invoice line items."""

    VAT_16 = "vat_16"  # 16% VAT (Kenya standard)
    VAT_8 = "vat_8"  # Legacy unscoped 8% records; not valid for new documents
    PETROLEUM_VAT_13 = "petroleum_vat_13"
    PETROLEUM_VAT_8 = "petroleum_vat_8"
    VAT_0 = "vat_0"  # 0% VAT (zero-rated)
    EXEMPT = "exempt"  # Exempt from VAT
    NO_TAX = "no_tax"  # No tax


#: Compatibility mapping from the profile-level TaxTreatment display values
#: onto the existing document-level TaxType members. Kept next to TaxType so
#: any change to either taxonomy is reviewed against the other.
_TAX_TREATMENT_TO_TAX_TYPE: dict["TaxTreatment", "TaxType"] = {
    TaxTreatment.VAT_16: TaxType.VAT_16,
    TaxTreatment.ZERO_RATED_EXPORT: TaxType.VAT_0,
    TaxTreatment.EXEMPT: TaxType.EXEMPT,
}


class DiscountType(StrEnum):
    """Discount calculation types."""

    AMOUNT = "amount"  # Fixed amount
    PERCENTAGE = "percentage"  # Percentage of subtotal


class QuoteStatus(StrEnum):
    """Quote status values."""

    DRAFT = "draft"
    SENT = "sent"
    APPROVED = "approved"
    INVOICED = "invoiced"
    EXPIRED = "expired"


class VendorStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class PayableTransactionStatus(StrEnum):
    PAID = "paid"
    PENDING = "pending"
    OVERDUE = "overdue"


class ExpenseStatus(StrEnum):
    """Expense lifecycle states."""

    PENDING = "pending"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELED = "canceled"


class DocumentSource(StrEnum):
    """Upload context for ExpenseDocument.source."""

    FORM = "form"
    VIEW = "view"
    PAYMENT_MODAL = "payment_modal"


class PurchaseOrderStatus(StrEnum):
    """Purchase Order lifecycle states.

    Lifecycle: DRAFT -> SENT -> PAID. There are no billed or canceled
    states for purchase orders.
    """

    DRAFT = "draft"  # Created but not yet sent
    SENT = "sent"  # Sent to the vendor (emailed or marked sent)
    PAID = "paid"  # Fully settled via recorded payments


class DocumentType(StrEnum):
    """Classification for purchase-order payment-modal attachments.

    Single source of truth consumed by the ORM model CHECK constraint,
    the Alembic migration, and the router validation — no raw string
    literals anywhere else.

    INVOICE : the bill/invoice being paid against.
    POP     : proof of payment (remittance advice, bank slip, etc.).
    OTHER   : PO-level attachments not linked to a specific payment.
    """

    INVOICE = "invoice"
    POP = "pop"
    OTHER = "other"

    @classmethod
    def db_check_values(cls) -> str:
        """Return the SQL IN-list literal for CHECK constraints.

        Usage in a CheckConstraint::

            CheckConstraint(
                f"document_type IN {DocumentType.db_check_values()}",
                name="ck_po_documents_valid_document_type",
            )
        """
        values = ", ".join(f"'{m.value}'" for m in cls)
        return f"({values})"
