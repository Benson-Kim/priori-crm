"""Deal API endpoints (Sales Desk pipeline)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.common.dependencies import DealQuoteServiceDep, DealServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import DealHygieneBucket, DealTab
from app.modules.deals.schemas import (
    DealActivityCreate,
    DealAdvanceRequest,
    DealCloseRequest,
    DealCreate,
    DealFilterParams,
    DealParkRequest,
    DealQuoteCreateRequest,
    DealQuotePreviewResponse,
    DealResponse,
    DealStatusCounts,
    DealUpdate,
)
from app.modules.quotes.schemas import QuoteResponse

logger = logging.getLogger(__name__)

router = APIRouter(route_class=CommitOnSuccessRoute)


@router.post(
    "",
    response_model=DealResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new deal",
    description=(
        "Create a pipeline deal for an existing customer. The deal currency "
        "must be one of the customer's billing-profile currencies (USD/KES)."
    ),
    responses={
        201: {"description": "Deal created successfully"},
        400: {
            "description": (
                "Customer inactive or currency not among the customer's "
                "billing profiles"
            )
        },
        404: {"description": "Customer or owner not found"},
    },
)
def create_deal(body: DealCreate, service: DealServiceDep) -> DealResponse:
    """Create a new deal (opens at Activation with its first record)."""
    deal = service.create(body, user_id=service.actor_id)
    return service.build_response(deal)


@router.get(
    "",
    response_model=PaginatedResponse[DealResponse],
    summary="List deals",
    description=(
        "Paginated pipeline list with tab (all|open|won|lost), owner, "
        "activity-hygiene bucket, show-closed and search filters. All "
        "derived fields (age_days, idle_days, weighted_value, latest "
        "record, stage history) are server-computed."
    ),
    responses={
        200: {"description": "List of deals"},
        400: {"description": "Invalid query parameters"},
    },
)
def list_deals(
    service: DealServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    tab: Annotated[
        DealTab,
        Query(description="Pipeline tab: all | open | won | lost"),
    ] = DealTab.ALL,
    owner_id: Annotated[
        UUID | None,
        Query(description="Filter by deal owner (sales rep)", alias="ownerId"),
    ] = None,
    customer_id: Annotated[
        UUID | None,
        Query(description="Filter by customer (company)", alias="customerId"),
    ] = None,
    hygiene: Annotated[
        DealHygieneBucket | None,
        Query(
            description=(
                "Activity-hygiene bucket (open deals only): active_week | "
                "quiet_8_30 | no_activity_30 | open_45"
            )
        ),
    ] = None,
    show_closed: Annotated[
        bool,
        Query(
            alias="showClosed",
            description="On the 'all' tab, include closed (won/lost) deals",
        ),
    ] = True,
    search: Annotated[
        str | None,
        Query(description="Search company name, contact or product"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[DealResponse]:
    """List deals with pagination and pipeline filters."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    filters = DealFilterParams(
        tab=tab,
        owner_id=owner_id,
        customer_id=customer_id,
        hygiene=hygiene,
        show_closed=show_closed,
        search=search,
    )
    return service.list_deals(params, filters)


@router.get(
    "/counts",
    response_model=DealStatusCounts,
    summary="Get deal status counts",
    description="Counts per pipeline tab: All (n) · Open (n) · Won (n) · Lost (n).",
    responses={200: {"description": "Deal counts by status"}},
)
def get_deal_counts(
    service: DealServiceDep,
    owner_id: Annotated[
        UUID | None,
        Query(description="Restrict counts to one owner", alias="ownerId"),
    ] = None,
) -> DealStatusCounts:
    """Get deal counts grouped by status."""
    return service.get_status_counts(owner_id)


@router.get(
    "/{deal_id}",
    response_model=DealResponse,
    summary="Get deal details",
    description=(
        "Full deal for the drawer: stage history, latest record, derived "
        "values and the billing line joined from the customer's billing "
        "profile matching the deal currency."
    ),
    responses={
        200: {"description": "Deal details"},
        404: {"description": "Deal not found"},
    },
)
def get_deal(deal_id: UUID, service: DealServiceDep) -> DealResponse:
    """Get detailed deal information by ID (linked quotes embedded)."""
    deal = service.get_by_id(deal_id)
    return service.build_response(deal, include_quotes=True)


@router.post(
    "/{deal_id}/quotes",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a quote from a deal (prefill flow)",
    description=(
        "Create a quote in the EXISTING quotes module, prefilled from the "
        "deal: customer from the deal, currency from the matching customer "
        "billing profile (defaults to the deal currency; EUR/GBP reference "
        "currencies are rejected with the typed ReferenceCurrencyException), "
        "line prices derived server-side from the org product catalog "
        "(annual billing -15%, extra discount %), line tax treatment from "
        "the billing profile, reference from the existing reference "
        "sequence, and all totals/VAT computed by the existing document "
        "services. The created quote is linked via deal_id and survives "
        "deal closure unchanged."
    ),
    responses={
        201: {"description": "Quote created and linked to the deal"},
        400: {
            "description": (
                "Reference (EUR/GBP) currency, unknown catalog product, "
                "closed deal, or invalid dates"
            )
        },
        404: {"description": "Deal or billing profile not found"},
    },
)
def create_deal_quote(
    deal_id: UUID,
    body: DealQuoteCreateRequest,
    service: DealQuoteServiceDep,
) -> QuoteResponse:
    """Create a quote from a deal via the existing quotes service."""
    quote = service.create_quote_for_deal(deal_id, body, user_id=service.actor_id)
    return QuoteResponse.model_validate(quote)


@router.post(
    "/{deal_id}/quotes/preview",
    response_model=DealQuotePreviewResponse,
    summary="Preview quote-builder pricing for a deal",
    description=(
        "Builder support (nothing persisted): per-line server-computed "
        "discounted per-user/month + list price, line totals with billing "
        "period, the totals block (subtotal, VAT line per the billing "
        "profile's tax treatment, grand total), the reference-currency "
        "conversions row (USD/EUR/GBP display equivalents) and the profile "
        "banner data. Totals are produced exclusively by the existing "
        "BaseDocumentService.calculate_totals delegation."
    ),
    responses={
        200: {"description": "Server-computed builder pricing"},
        400: {"description": "Reference currency or unknown catalog product"},
        404: {"description": "Deal or billing profile not found"},
    },
)
def preview_deal_quote(
    deal_id: UUID,
    body: DealQuoteCreateRequest,
    service: DealQuoteServiceDep,
) -> DealQuotePreviewResponse:
    """Preview server-computed quote pricing for the builder."""
    return service.preview_quote_for_deal(deal_id, body)


@router.put(
    "/{deal_id}",
    response_model=DealResponse,
    summary="Update deal",
    description=(
        "Update commercial fields (owner, product, seats, value, currency) "
        "of an open or parked deal. Closed deals are read-only."
    ),
    responses={
        200: {"description": "Deal updated successfully"},
        400: {"description": "Deal closed or currency has no billing profile"},
        404: {"description": "Deal not found"},
        409: {"description": "Version conflict (concurrent edit detected)"},
    },
)
def update_deal(
    deal_id: UUID,
    body: DealUpdate,
    service: DealServiceDep,
    expected_version: Annotated[
        int,
        Query(description="Expected version number for optimistic locking"),
    ],
) -> DealResponse:
    """Update an existing deal."""
    deal = service.update(deal_id, body, expected_version)
    return service.build_response(deal)


@router.delete(
    "/{deal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete deal",
    description="Delete an open/parked deal. Closed deals are history and stay.",
    responses={
        204: {"description": "Deal deleted successfully"},
        400: {"description": "Deal is closed (won/lost)"},
        404: {"description": "Deal not found"},
    },
)
def delete_deal(deal_id: UUID, service: DealServiceDep) -> None:
    """Delete a deal (open or parked only)."""
    service.delete(deal_id)


@router.post(
    "/{deal_id}/advance",
    response_model=DealResponse,
    summary="Advance deal one stage",
    description=(
        "Move an open deal to the next stage (no skipping). A non-empty "
        "note is required — every stage keeps a record."
    ),
    responses={
        200: {"description": "Deal advanced"},
        400: {"description": "Deal closed/parked or already at the final stage"},
        404: {"description": "Deal not found"},
        422: {"description": "Note missing or empty"},
    },
)
def advance_deal_stage(
    deal_id: UUID, body: DealAdvanceRequest, service: DealServiceDep
) -> DealResponse:
    """Advance a deal to the next stage (note mandatory)."""
    deal = service.advance_stage(deal_id, body.note)
    return service.build_response(deal)


@router.post(
    "/{deal_id}/activities",
    response_model=DealResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log deal activity",
    description="Append a stage record at the current stage of an open deal.",
    responses={
        201: {"description": "Activity logged"},
        400: {"description": "Deal is not open"},
        404: {"description": "Deal not found"},
        422: {"description": "Note missing or empty"},
    },
)
def log_deal_activity(
    deal_id: UUID, body: DealActivityCreate, service: DealServiceDep
) -> DealResponse:
    """Log an activity note on an open deal."""
    deal = service.log_activity(deal_id, body.note)
    return service.build_response(deal)


@router.post(
    "/{deal_id}/close",
    response_model=DealResponse,
    summary="Close deal won or lost",
    description=(
        "Close an open deal with an enumerated won/lost reason and a mandatory note."
    ),
    responses={
        200: {"description": "Deal closed"},
        400: {"description": "Deal not open or invalid reason for the result"},
        404: {"description": "Deal not found"},
        422: {"description": "Note missing or empty"},
    },
)
def close_deal(
    deal_id: UUID, body: DealCloseRequest, service: DealServiceDep
) -> DealResponse:
    """Close a deal won/lost with reason + note."""
    deal = service.close(deal_id, body.result, body.reason, body.note)
    return service.build_response(deal)


@router.post(
    "/{deal_id}/park",
    response_model=DealResponse,
    summary="Park deal (move to future pipeline)",
    description=(
        "Park an open deal until a future re-engage date. The deal keeps "
        "its stage as resume_stage and returns there on resume."
    ),
    responses={
        200: {"description": "Deal parked"},
        400: {"description": "Deal not open or re-engage date not in the future"},
        404: {"description": "Deal not found"},
        422: {"description": "Note missing or empty"},
    },
)
def park_deal(
    deal_id: UUID, body: DealParkRequest, service: DealServiceDep
) -> DealResponse:
    """Park a deal into the future pipeline."""
    deal = service.park(deal_id, body.parked_until, body.note)
    return service.build_response(deal)


@router.post(
    "/{deal_id}/resume",
    response_model=DealResponse,
    summary="Resume parked deal",
    description="Re-engage a parked deal back to the stage it was parked at.",
    responses={
        200: {"description": "Deal resumed"},
        400: {"description": "Deal is not parked"},
        404: {"description": "Deal not found"},
    },
)
def resume_deal(deal_id: UUID, service: DealServiceDep) -> DealResponse:
    """Resume a parked deal from the future pipeline."""
    deal = service.resume(deal_id)
    return service.build_response(deal)
