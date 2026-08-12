"""Sales Desk <-> Quotes integration (issue #44).

A thin prefill/projection layer over the EXISTING quotes module — there is
deliberately no parallel quote entity and NO pricing/tax math here beyond
deriving per-seat *prices* from the org catalog:

- Quote rows are created exclusively through ``QuoteService.create`` (which
  owns references via the reference_sequence generator, line building,
  discounts and VAT).
- Totals/VAT — including builder previews — are computed exclusively by the
  inherited ``BaseDocumentService.calculate_totals`` delegation and the
  shared sales VAT rules (``common/financial.py``); the line tax treatment
  is derived from the billing profile's ``tax_treatment`` (never a
  hard-coded rate).
- Currency conversions come from the shared display-only FX conventions
  (``common/fx.py``).

Price derivation (the only arithmetic that lives here, because it is a
*price list* concern, not a document-total concern):

    list/user/month      = catalog USD price x FX units-per-USD   (2 dp)
    discounted/user/month = list x (1 - annual%) x (1 - extra%)   (2 dp)
    unit_price (per seat) = discounted/user/month x months (1 or 12)

calibrated verbatim against ``App__3_.svg`` (Business Basic $6.00 ->
KSh 777.00 list, KSh 660.45 annual, KSh 79,254.00 line total for 10 seats).
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from app.common.document_service import ServiceBase
from app.common.exceptions import (
    BadRequestException,
    NotFoundException,
    ReferenceCurrencyException,
)
from app.common.financial import quantize_money
from app.common.fx import reference_conversions, units_per_usd, usd_equivalent
from app.common.reporting_time import reporting_date
from app.constants.enums import (
    BillingCurrency,
    BillingPeriod,
    QuoteDisplayStatus,
    TaxTreatment,
)
from app.constants.settings_defaults import (
    DEFAULT_ANNUAL_BILLING_DISCOUNT_PCT,
    DEFAULT_PRODUCT_CATALOG,
)
from app.modules.customers.models import CustomerBillingProfile
from app.modules.deals.models import Deal
from app.modules.deals.schemas import (
    DealBillingProfileResponse,
    DealQuoteConversionsRow,
    DealQuoteCreateRequest,
    DealQuoteLineInput,
    DealQuoteLinePreview,
    DealQuotePreviewResponse,
    DealQuoteSummary,
)
from app.modules.quotes.models import Quote

logger = logging.getLogger(__name__)

#: Days until a prefilled quote expires when the client sends no due date.
DEFAULT_QUOTE_VALIDITY_DAYS = 30

_PERIOD_MONTHS = {BillingPeriod.MONTHLY: 1, BillingPeriod.ANNUAL: 12}
_PERIOD_LABELS = {BillingPeriod.MONTHLY: "per month", BillingPeriod.ANNUAL: "per year"}


class DealQuoteService(ServiceBase):
    """Create, preview and project quotes for a Sales Desk deal."""

    # PUBLIC API

    def create_quote_for_deal(
        self,
        deal_id: uuid.UUID,
        body: DealQuoteCreateRequest,
        user_id: uuid.UUID | None = None,
    ) -> Quote:
        """Prefill-create a quote from a deal via the existing quotes service.

        Customer comes from the deal; currency from the matching customer
        billing profile (EUR/GBP reference currencies -> typed 400); line
        tax treatment from the profile; reference/totals/VAT entirely from
        ``QuoteService.create``.
        """
        from app.modules.quotes.service import QuoteService

        deal = self._get_deal(deal_id)
        if deal.is_closed:
            raise BadRequestException(
                detail=(
                    "Cannot create a quote for a closed deal. Existing "
                    "quotes are unaffected by deal closure."
                ),
                field="deal_id",
            )

        profile = self._resolve_profile(deal, body.currency)
        payload = self._build_quote_payload(deal, profile, body)

        quote = QuoteService(self._db, current_user=self._current_user).create(
            payload, user_id=user_id, deal_id=deal.id
        )
        logger.info(
            "Created quote %s from deal",
            quote.quote_number,
            extra={
                "quote_id": str(quote.id),
                "deal_id": str(deal.id),
                "profile_code": profile.code,
            },
        )
        return quote

    def preview_quote_for_deal(
        self, deal_id: uuid.UUID, body: DealQuoteCreateRequest
    ) -> DealQuotePreviewResponse:
        """Builder support: server-computed line prices + delegated totals.

        Nothing is persisted. Totals come from the shared
        ``BaseDocumentService.calculate_totals`` (via QuoteService) so the
        preview can never drift from what create() would persist.
        """
        from app.modules.quotes.service import QuoteService

        deal = self._get_deal(deal_id)
        profile = self._resolve_profile(deal, body.currency)
        transaction_date = reporting_date()

        line_items = self._derive_line_items(body.lines, profile)
        totals = QuoteService.calculate_totals(
            line_items, tax_point_date=transaction_date
        )

        previews = []
        for line, item, calculated in zip(
            body.lines, line_items, totals.line_items, strict=True
        ):
            previews.append(
                DealQuoteLinePreview(
                    product=line.product,
                    seats=line.seats,
                    billing_period=line.billing_period,
                    extra_discount_pct=line.extra_discount_pct,
                    list_price_per_user_month=item["list_price_per_user_month"],
                    discounted_price_per_user_month=item[
                        "discounted_price_per_user_month"
                    ],
                    months=_PERIOD_MONTHS[line.billing_period],
                    unit_price=item["unit_price"],
                    line_total=Decimal(str(calculated["line_total"])),
                    period_label=_PERIOD_LABELS[line.billing_period],
                    tax_type=item["tax_type"],
                )
            )

        conversions = reference_conversions(totals.total_due, profile.currency)
        return DealQuotePreviewResponse(
            currency=profile.currency,
            lines=previews,
            subtotal=totals.subtotal,
            tax_label=profile.tax_treatment,
            tax_total=totals.tax_total,
            total_due=totals.total_due,
            conversions=DealQuoteConversionsRow(
                usd=conversions["USD"],
                eur=conversions["EUR"],
                gbp=conversions["GBP"],
            ),
            billing_profile=DealBillingProfileResponse.model_validate(profile),
        )

    def quotes_for_deal(self, deal: Deal) -> list[DealQuoteSummary]:
        """Project the deal's linked quotes for the detail embed (newest first).

        Quotes survive deal closure unchanged — this reads whatever rows
        point at the deal, regardless of deal status.
        """
        quotes = (
            self._db.query(Quote)
            .filter(Quote.deal_id == deal.id)
            .order_by(Quote.created_at.desc())
            .all()
        )
        if not quotes:
            return []

        profile_codes = {p.currency: p.code for p in deal.customer.billing_profiles}
        return [
            DealQuoteSummary(
                id=quote.id,
                quote_number=quote.quote_number,
                quote_reference=quote.quote_reference,
                deal_id=quote.deal_id,
                customer_id=quote.customer_id,
                company_name=deal.customer.display_name,
                billing_profile_code=profile_codes.get(quote.currency),
                currency=quote.currency,
                total_due=quote.total_due,
                total_usd_equivalent=usd_equivalent(quote.total_due, quote.currency),
                transaction_date=quote.transaction_date,
                due_date=quote.due_date,
                status=quote.status,
                display_status=QuoteDisplayStatus.from_quote_status(quote.status),
                created_at=quote.created_at,
            )
            for quote in quotes
        ]

    # INTERNALS

    def _get_deal(self, deal_id: uuid.UUID) -> Deal:
        deal = self._db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise NotFoundException(
                detail=f"Deal with ID '{deal_id}' not found", resource="deal"
            )
        return deal

    def _resolve_profile(
        self, deal: Deal, currency: str | None
    ) -> CustomerBillingProfile:
        """Resolve the billing profile the quote bills against.

        Defaults to the deal currency. EUR/GBP (any non-billing-profile
        currency) raise the typed ReferenceCurrencyException.
        """
        requested = str(currency) if currency is not None else deal.currency
        billable = [m.value for m in BillingCurrency]
        if requested not in billable:
            raise ReferenceCurrencyException(requested, billable)

        profile = (
            self._db.query(CustomerBillingProfile)
            .filter(
                CustomerBillingProfile.customer_id == deal.customer_id,
                CustomerBillingProfile.currency == requested,
            )
            .first()
        )
        if profile is None:
            raise NotFoundException(
                detail=(
                    f"Customer has no {requested} billing profile to bill "
                    "this quote against"
                ),
                resource="billing_profile",
            )
        return profile

    def _build_quote_payload(
        self,
        deal: Deal,
        profile: CustomerBillingProfile,
        body: DealQuoteCreateRequest,
    ):
        """Assemble the QuoteCreate payload for the existing quotes service."""
        from app.modules.quotes.schemas import QuoteCreate

        transaction_date = reporting_date()
        due_date = body.due_date or transaction_date + timedelta(
            days=DEFAULT_QUOTE_VALIDITY_DAYS
        )
        if due_date < transaction_date:
            raise BadRequestException(
                detail="due_date must be on or after today", field="due_date"
            )

        return QuoteCreate(
            customer_id=deal.customer_id,
            transaction_date=transaction_date,
            due_date=due_date,
            currency=profile.currency,
            line_items=self._derive_line_items(body.lines, profile),
            notes=body.notes,
            # Per-line tax (from the profile treatment) carries the VAT;
            # explicitly disable document-level VAT so the owner-profile
            # default can never double-tax the document.
            vat_enabled=False,
        )

    def _derive_line_items(
        self,
        lines: list[DealQuoteLineInput],
        profile: CustomerBillingProfile,
    ) -> list[dict]:
        """Derive priced line items from the org catalog (prices only).

        Returns dicts shaped for ``QuoteCreate.line_items`` /
        ``calculate_totals`` plus the per-user/month support values the
        builder renders. Tax comes solely from the profile's tax
        treatment; document totals/VAT are computed downstream by the
        existing quotes service.
        """
        catalog = {
            entry["name"]: Decimal(entry["usd_per_seat_month"])
            for entry in DEFAULT_PRODUCT_CATALOG
        }
        annual_pct = Decimal(DEFAULT_ANNUAL_BILLING_DISCOUNT_PCT)
        fx_rate = units_per_usd(profile.currency)
        tax_type = TaxTreatment(profile.tax_treatment).tax_type

        items: list[dict] = []
        for line in lines:
            usd_list = catalog.get(line.product)
            if usd_list is None:
                raise BadRequestException(
                    detail=(
                        f"'{line.product}' is not in the product catalog. "
                        f"Available products: {sorted(catalog)}"
                    ),
                    field="product",
                )

            list_per_user_month = quantize_money(usd_list * fx_rate)
            multiplier = Decimal("1")
            if line.billing_period == BillingPeriod.ANNUAL:
                multiplier *= Decimal("1") - annual_pct / Decimal("100")
            multiplier *= Decimal("1") - line.extra_discount_pct / Decimal("100")

            discounted_per_user_month = quantize_money(list_per_user_month * multiplier)
            months = _PERIOD_MONTHS[line.billing_period]
            unit_price = discounted_per_user_month * months

            period = (
                "Annual" if line.billing_period == BillingPeriod.ANNUAL else "Monthly"
            )
            description = (
                f"{period} billing - {line.seats} seats x {profile.currency} "
                f"{discounted_per_user_month}/user/month"
                f" (list {list_per_user_month})"
                f" x {months} month{'s' if months > 1 else ''}"
            )

            items.append(
                {
                    "item_name": line.product,
                    "description": description,
                    "quantity": Decimal(line.seats),
                    "unit_price": unit_price,
                    "tax_type": tax_type,
                    # Builder support values (ignored by QuoteCreate /
                    # build_line_items, consumed by the preview projection).
                    "list_price_per_user_month": list_per_user_month,
                    "discounted_price_per_user_month": discounted_per_user_month,
                }
            )
        return items
