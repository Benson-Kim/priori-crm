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
    VAT_8 = "vat_8"  # 8% VAT (Kenya petroleum/fuel)
    VAT_0 = "vat_0"  # 0% VAT (zero-rated)
    EXEMPT = "exempt"  # Exempt from VAT
    NO_TAX = "no_tax"  # No tax


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

    NOTE: the cancelled state is spelled 'cancelled' (two L's) per the
    PO-01 specification, which deliberately differs from the rest of the
    codebase where the equivalent state is spelled 'canceled' (one L,
    e.g. ExpenseStatus.CANCELED, InvoiceStatus.CANCELED). Reviewers should
    be aware of this intentional inconsistency.
    """

    DRAFT = "draft"
    SENT = "sent"
    BILLED = "billed"
    CANCELLED = "cancelled"
