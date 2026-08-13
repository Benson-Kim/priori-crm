"""Application-wide enumerations for type safety."""

from decimal import Decimal
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


class ModuleKey(StrEnum):
    """Per-owner feature-toggleable application modules.

    Each key identifies a functional module whose availability can be
    switched on/off per owner profile via ``owner_module_settings``.
    A missing settings row means the module is ENABLED (default-on).

    Members listed in :data:`ESSENTIAL_MODULES` are core infrastructure
    (auth, owner profile, health, dashboard) and can never be disabled —
    their routers carry no module gate and the admin API rejects attempts
    to disable them with a 422.
    """

    # Toggleable business modules
    CUSTOMERS = "customers"
    QUOTES = "quotes"
    INVOICES = "invoices"
    DEALS = "deals"
    NURTURE = "nurture"
    ONBOARDING = "onboarding"
    VENDORS = "vendors"
    EXPENSES = "expenses"
    PURCHASE_ORDERS = "purchase_orders"
    STATEMENTS = "statements"
    REPORTS = "reports"

    # Essential modules — never disableable, no router gate
    AUTH = "auth"
    OWNER = "owner"
    HEALTH = "health"
    DASHBOARD = "dashboard"

    @property
    def is_essential(self) -> bool:
        """Whether this module is core infrastructure that cannot be disabled."""
        return self in ESSENTIAL_MODULES


#: Modules that can never be disabled. Their routers carry no
#: require_module gate and PATCH /owner/modules rejects them with 422.
ESSENTIAL_MODULES: frozenset["ModuleKey"] = frozenset(
    {
        ModuleKey.AUTH,
        ModuleKey.OWNER,
        ModuleKey.HEALTH,
        ModuleKey.DASHBOARD,
    }
)


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


class DealStage(StrEnum):
    """Open-pipeline stages of a Sales Desk deal, in order.

    A deal moves strictly one stage at a time (no skipping) and every
    advance requires a note. The UI renders "Stage N of 5" where the fifth
    step is the closed (won/lost) state carried by :class:`DealStatus`, so
    the stage enum itself only holds the four open stages.
    """

    ACTIVATION = "activation"
    QUALIFICATION = "qualification"
    PROPOSAL_QUOTE = "proposal_quote"
    NEGOTIATION = "negotiation"

    @property
    def pipeline_index(self) -> int:
        """1-based position in the pipeline (UI: "Stage N of 5").

        Named ``pipeline_index`` (not ``index``) to avoid shadowing
        ``str.index`` on this str-mixin enum.
        """
        return _DEAL_STAGE_ORDER.index(self) + 1

    @property
    def label(self) -> str:
        """Human display label (verbatim from the Sales Desk designs)."""
        return _DEAL_STAGE_LABELS[self]

    @property
    def probability(self) -> Decimal:
        """Win probability used for the weighted pipeline value.

        Calibrated against the Pipeline.svg "Weighted" column:
        $2,700→$270 (10%), $5,760→$1,440 (25%), $11,880→$5,940 (50%),
        $51,840→$38,880 (75%).
        """
        return _DEAL_STAGE_PROBABILITIES[self]

    @property
    def next_stage(self) -> "DealStage | None":
        """The following stage, or None from the final open stage."""
        idx = _DEAL_STAGE_ORDER.index(self)
        if idx + 1 >= len(_DEAL_STAGE_ORDER):
            return None
        return _DEAL_STAGE_ORDER[idx + 1]


#: Canonical stage ordering — the single source for index/next-stage logic.
_DEAL_STAGE_ORDER: tuple["DealStage", ...] = (
    DealStage.ACTIVATION,
    DealStage.QUALIFICATION,
    DealStage.PROPOSAL_QUOTE,
    DealStage.NEGOTIATION,
)

_DEAL_STAGE_LABELS: dict["DealStage", str] = {
    DealStage.ACTIVATION: "Activation",
    DealStage.QUALIFICATION: "Qualification",
    DealStage.PROPOSAL_QUOTE: "Proposal & Quote",
    DealStage.NEGOTIATION: "Negotiation",
}

_DEAL_STAGE_PROBABILITIES: dict["DealStage", Decimal] = {
    DealStage.ACTIVATION: Decimal("0.10"),
    DealStage.QUALIFICATION: Decimal("0.25"),
    DealStage.PROPOSAL_QUOTE: Decimal("0.50"),
    DealStage.NEGOTIATION: Decimal("0.75"),
}

#: Total steps the pipeline UI renders ("Stage N of 5"): the four open
#: stages plus the terminal closed (won/lost) step.
DEAL_PIPELINE_TOTAL_STEPS: int = len(_DEAL_STAGE_ORDER) + 1


class DealStatus(StrEnum):
    """Lifecycle status of a deal.

    OPEN deals sit in the pipeline; PARKED deals live in the future
    pipeline (with ``parked_until``/``resume_stage``); WON/LOST are the
    terminal closed states carrying a close reason + note.
    """

    OPEN = "open"
    WON = "won"
    LOST = "lost"
    PARKED = "parked"


class DealWonReason(StrEnum):
    """Enumerated 'main reason we won' options (deal-desk parity)."""

    PRICE_PACKAGING = "Price & packaging"
    RELATIONSHIP_TRUST = "Relationship / trust"
    PRODUCT_FIT = "Product fit"
    MIGRATION_SUPPORT_OFFER = "Migration & support offer"
    FASTER_RESPONSE = "Faster response than competitor"


class DealLostReason(StrEnum):
    """Enumerated 'main reason we lost' options (deal-desk parity)."""

    PRICE_TOO_HIGH = "Price too high"
    LOST_TO_COMPETITOR = "Lost to competitor"
    BAD_TIMING = "Bad timing"
    NO_BUDGET = "No budget"
    WENT_DIRECT_TO_MICROSOFT = "Went direct to Microsoft"
    NO_DECISION = "No decision / went silent"


class DealHygieneBucket(StrEnum):
    """Activity-hygiene list filters for the pipeline (open deals only).

    Mirrors the design's filter chips: Active this week / 8-30 days quiet /
    No activity 30d+ / Open 45d+.
    """

    ACTIVE_WEEK = "active_week"  # last activity within 7 days
    QUIET_8_30 = "quiet_8_30"  # last activity 8-30 days ago
    NO_ACTIVITY_30 = "no_activity_30"  # last activity more than 30 days ago
    OPEN_45 = "open_45"  # in the pipeline for more than 45 days


class DealTab(StrEnum):
    """Pipeline list tabs: All (6) · Open (4) · Won (1) · Lost (1)."""

    ALL = "all"
    OPEN = "open"
    WON = "won"
    LOST = "lost"


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


class QuoteDisplayStatus(StrEnum):
    """Sales Desk display state of a quote (Quotes & pricing screen, #44).

    The design exports (``Quotes___Pricing.svg`` / ``App__3_.svg``) render
    exactly three states, mapped from the existing quote lifecycle:

    - ``Draft``   <- ``draft`` (still being built) and ``expired``
      (the offer lapsed; it needs rework before it can be re-sent)
    - ``Pending`` <- ``sent`` and ``approved`` (in flight with the
      customer; not yet posted to accounting)
    - ``Synced``  <- ``invoiced`` (converted to an invoice, i.e. pushed
      through to accounting)

    Display-only: the persisted lifecycle (:class:`QuoteStatus`) remains
    the source of truth and is always returned alongside this value.
    """

    DRAFT = "Draft"
    PENDING = "Pending"
    SYNCED = "Synced"

    @classmethod
    def from_quote_status(cls, status: "QuoteStatus | str") -> "QuoteDisplayStatus":
        """Map the persisted quote lifecycle onto the 3 display states."""
        return _QUOTE_STATUS_TO_DISPLAY[QuoteStatus(status)]


#: Lifecycle -> display-state mapping (kept next to the enums so a change
#: to either is reviewed against the other). Exhaustive over QuoteStatus.
_QUOTE_STATUS_TO_DISPLAY: dict[QuoteStatus, QuoteDisplayStatus] = {
    QuoteStatus.DRAFT: QuoteDisplayStatus.DRAFT,
    QuoteStatus.SENT: QuoteDisplayStatus.PENDING,
    QuoteStatus.APPROVED: QuoteDisplayStatus.PENDING,
    QuoteStatus.INVOICED: QuoteDisplayStatus.SYNCED,
    QuoteStatus.EXPIRED: QuoteDisplayStatus.DRAFT,
}


class BillingPeriod(StrEnum):
    """Billing period of a quote-builder line (Sales Desk, issue #44).

    ``annual`` applies the org's annual billing discount
    (``annual_billing_discount_pct``, default 15) to the per-user/month
    price and bills 12 months per seat; ``monthly`` bills 1 month at list.
    """

    MONTHLY = "monthly"
    ANNUAL = "annual"


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
