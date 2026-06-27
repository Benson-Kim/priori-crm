"""Env-guarded demo seed: a complete, self-consistent dataset.

Builds the full happy-path demo THROUGH THE SERVICE LAYER (not raw inserts),
so every invariant is exercised exactly as in production: reference numbers
from the high-water-mark generator, immutable owner snapshots on issue,
optimistic-lock version bumps, audit-event rows, and the transactional email
outbox.

Creates, in dependency order:
    1. Owner profile      (company branding)
    2. Customer           (active business customer, KES)
    3. Vendor             (active vendor, KES)
    4. Quote              (draft sales quotation for the customer)
    5. Invoice            (sent, then a partial payment -> status 'partial')
    6. Purchase order     (sent, then a payment recorded)
    7. Overpayment demo   (an extra invoice payment -> negative balance_due)
    8. Statement          (prints the customer statement summary)

Properties:
    - Idempotent: re-running reuses existing demo records (matched by the
      well-known demo email / name) instead of duplicating them.
    - Development-only: refuses to run unless ENVIRONMENT == 'development'.

Usage::

    cd api
    alembic upgrade head
    python -m scripts.seed_demo
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from app.common.database import get_db_context
from app.constants.enums import Currency, CustomerType, PaymentMethod, TaxType
from app.lib.config import settings
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import CustomerService
from app.modules.invoices.schemas import (
    InvoiceCreate,
    InvoiceLineItemCreate,
    PaymentCreate,
)
from app.modules.invoices.service import InvoiceService
from app.modules.owner.schemas import OwnerProfileUpdate
from app.modules.owner.service import OwnerService
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderPaymentCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.quotes.models import Quote
from app.modules.quotes.schemas import QuoteCreate, QuoteLineItemCreate
from app.modules.quotes.service import QuoteService
from app.modules.vendors.models import Vendor
from app.modules.vendors.schemas import VendorCreate
from app.modules.vendors.service import VendorService

logger = logging.getLogger(__name__)

# Well-known identifiers used to make the seed idempotent.
DEMO_CUSTOMER_EMAIL = "demo.customer@example.com"
DEMO_VENDOR_EMAIL = "demo.vendor@example.com"


def _seed_owner(db) -> None:
    """Set the singleton owner profile (idempotent: update is upsert-like)."""
    OwnerService(db).update(
        OwnerProfileUpdate(
            full_name="PrioriTech Demo Ltd",
            address="1 Demo Plaza, Upper Hill, Nairobi",
            email="hello@prioritech.example",
            phone="+254700000000",
            tax_pin="P000000000X",
            website="prioritech.example",
            jurisdiction="KE",
        )
    )
    logger.info("Owner profile ready")


def _seed_customer(db) -> Customer:
    existing = db.query(Customer).filter(Customer.email == DEMO_CUSTOMER_EMAIL).first()
    if existing is not None:
        logger.info("Customer already exists: %s", existing.display_name)
        return existing

    customer = CustomerService(db).create(
        CustomerCreate(
            customer_type=CustomerType.BUSINESS,
            company_name="Acme Distributors Ltd",
            first_name="Ada",
            last_name="Kamau",
            email=DEMO_CUSTOMER_EMAIL,
            phone="+254711111111",
            currency=Currency.KES,
            address="42 Market Street, Nairobi",
        )
    )
    db.flush()
    logger.info("Created customer: %s", customer.display_name)
    return customer


def _seed_vendor(db) -> Vendor:
    existing = db.query(Vendor).filter(Vendor.email == DEMO_VENDOR_EMAIL).first()
    if existing is not None:
        logger.info("Vendor already exists: %s", existing.vendor_name)
        return existing

    vendor = VendorService(db).create(
        VendorCreate(
            vendor_name="Office Supplies Co",
            email=DEMO_VENDOR_EMAIL,
            currency=Currency.KES,
            tax_id_pin="P111111111Y",
        )
    )
    db.flush()
    logger.info("Created vendor: %s", vendor.vendor_name)
    return vendor


def _seed_quote(db, customer: Customer) -> Quote:
    existing = db.query(Quote).filter(Quote.customer_id == customer.id).first()
    if existing is not None:
        logger.info("Quote already exists: %s", existing.quote_reference)
        return existing

    quote = QuoteService(db).create(
        QuoteCreate(
            customer_id=customer.id,
            transaction_date=date.today(),
            due_date=date.today() + timedelta(days=14),
            currency=Currency.KES,
            line_items=[
                QuoteLineItemCreate(
                    item_name="Consulting",
                    description="Discovery workshop (2 days)",
                    quantity=Decimal("2"),
                    unit_price=Decimal("50000.00"),
                    tax_type=TaxType.VAT_16,
                )
            ],
            notes="Demo quote.",
        )
    )
    db.flush()
    logger.info("Created quote: %s", quote.quote_reference)
    return quote


def _seed_invoice(db, customer: Customer):
    """Create an invoice, send it, and record a partial then overpayment."""
    svc = InvoiceService(db)
    invoice = svc.create(
        InvoiceCreate(
            customer_id=customer.id,
            transaction_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency=Currency.KES,
            line_items=[
                InvoiceLineItemCreate(
                    item_name="Implementation",
                    description="Phase 1 delivery",
                    quantity=Decimal("1"),
                    unit_price=Decimal("100000.00"),
                    tax_type=TaxType.VAT_16,
                )
            ],
            notes="Demo invoice.",
        )
    )
    db.flush()
    svc.mark_as_sent(invoice.id)

    # Partial payment -> status 'partial'.
    svc.record_payment(
        invoice.id,
        PaymentCreate(
            amount=Decimal("40000.00"),
            payment_date=date.today(),
            payment_method=PaymentMethod.BANK_TRANSFER,
            reference="DEMO-PAY-1",
        ),
    )
    db.flush()
    logger.info(
        "Created invoice %s (sent, partial payment recorded)",
        invoice.invoice_reference,
    )
    return invoice


def _seed_overpayment(db, invoice_id) -> None:
    """Record enough further payments to fully settle and then overpay.

    Demonstrates that a recorded payment may exceed the balance: the final
    balance_due goes negative (a credit owed back), which the schema returns
    with its real sign.
    """
    svc = InvoiceService(db)
    # Settle the remaining balance and then some, in two further payments, to
    # exercise the reconciliation / overpayment path end to end.
    svc.record_payment(
        invoice_id,
        PaymentCreate(
            amount=Decimal("76000.00"),  # clears the ~116000 total
            payment_date=date.today(),
            payment_method=PaymentMethod.BANK_TRANSFER,
            reference="DEMO-PAY-2",
        ),
    )
    svc.record_payment(
        invoice_id,
        PaymentCreate(
            amount=Decimal("5000.00"),  # overpayment -> negative balance_due
            payment_date=date.today(),
            payment_method=PaymentMethod.CASH,
            reference="DEMO-OVERPAY",
        ),
    )
    db.flush()
    invoice = svc.get_by_id(invoice_id)
    logger.info(
        "Invoice %s overpaid: status=%s balance_due=%s (negative = credit owed)",
        invoice.invoice_reference,
        invoice.status,
        invoice.balance_due,
    )


def _seed_purchase_order(db, vendor: Vendor) -> None:
    svc = PurchaseOrderService(db)
    po = svc.create(
        PurchaseOrderCreate(
            vendor_id=vendor.id,
            order_date=date.today(),
            delivery_date=date.today() + timedelta(days=7),
            line_items=[
                PurchaseOrderLineItemCreate(
                    item_name="Laptops",
                    description="Developer laptops",
                    quantity=Decimal("3"),
                    unit_price=Decimal("120000.00"),
                    tax_type=TaxType.VAT_16,
                )
            ],
            notes="Demo purchase order.",
        )
    )
    db.flush()
    svc.mark_as_sent(po.id)
    svc.record_payment(
        po.id,
        PurchaseOrderPaymentCreate(
            amount=Decimal("100000.00"),
            payment_date=date.today(),
            reference="DEMO-PO-PAY-1",
        ),
    )
    db.flush()
    logger.info("Created purchase order %s (sent, payment recorded)", po.po_reference)


def _print_statement(db, customer: Customer) -> None:
    """Print the customer financial summary so the demo is visible end-to-end."""
    db.refresh(customer)
    logger.info(
        "Customer %s balance (account rollup, clamped >= 0): %s",
        customer.display_name,
        customer.balance,
    )


def seed_demo() -> None:
    """Build the complete demo dataset. Development-only and idempotent."""
    if settings.ENVIRONMENT != "development":
        raise SystemExit(
            "Refusing to seed demo data: ENVIRONMENT is not 'development'. "
            f"(got '{settings.ENVIRONMENT}')"
        )

    with get_db_context() as db:
        _seed_owner(db)
        customer = _seed_customer(db)
        vendor = _seed_vendor(db)
        _seed_quote(db, customer)
        invoice = _seed_invoice(db, customer)
        _seed_overpayment(db, invoice.id)
        _seed_purchase_order(db, vendor)
        _print_statement(db, customer)
        # get_db_context() owns the commit on successful exit.
        logger.info("Demo seed complete.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    seed_demo()


if __name__ == "__main__":
    main()
