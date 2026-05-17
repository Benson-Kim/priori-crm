"""Application-wide enumerations for type safety."""
from enum import StrEnum


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
    
    DRAFT = "draft"          # Created but not sent
    SENT = "sent"            # Sent to customer, awaiting payment
    PARTIAL = "partial"      # Partially paid
    PAID = "paid"            # Fully paid
    OVERDUE = "overdue"      # Past due date, unpaid
    CANCELED = "canceled"    # Voided/cancelled (immutable)


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
    
    VAT_16 = "vat_16"        # 16% VAT (Kenya standard)
    VAT_0 = "vat_0"          # 0% VAT (exempt)
    NO_TAX = "no_tax"        # No tax


class DiscountType(StrEnum):
    """Discount calculation types."""
    
    AMOUNT = "amount"        # Fixed amount
    PERCENTAGE = "percentage" # Percentage of subtotal


class QuoteStatus(StrEnum):
    """Quote status values."""
    DRAFT = "draft"
    SENT = "sent"
    APPROVED = "approved"
    INVOICED = "invoiced"
    EXPIRED = "expired"