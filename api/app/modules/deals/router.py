"""Sales Desk deals API endpoints (issue #39).

CRUD plus the pipeline lifecycle: advance stage, log activity, close
won/lost, park into and resume from the future pipeline. Advancing,
closing, logging and parking all require a non-blank note and answer 422
otherwise — 'A note is required to advance or close.'
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.common.dependencies import DealServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
from app.modules.deals.schemas import (
    DealActivityCreate,
    DealAdvanceRequest,
    DealAggregatesResponse,
    DealCloseRequest,
    DealCreate,
    DealDeleteResponse,
    DealParkRequest,
    DealResponse,
    DealSummary,
    DealUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new deal",
    description=(
        "Create a deal for an existing customer. The deal value currency "
        "follows the customer's primary_currency billing profile (USD|KES) "
        "unless explicitly overridden. Writes the opening stage record."
    ),
    responses={
        201: {"description": "Deal created"},
        404: {"description": "Customer or owner not found"},
        422: {"description": "Validation failed"},
    },
)
def create_deal(body: DealCreate, service: DealServiceDep) -> DealResponse:
    """Create a deal plus its opening Activation stage record."""
    deal = service.create(body)
    return service.to_response(deal)


@router.get(
    "",
    summary="List deals",
    description=(
        "Paginated pipeline list with derived metrics (age, idle days, "
        "time in pipeline, 'Stage N of 5' progress) and USD-equivalent "
        "values for aggregation. Default order: open deals first (later "
        "stages first, larger values first), parked next, closed last."
    ),
    responses={200: {"description": "Paginated deal list"}},
)
def list_deals(
    service: DealServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=100, description="Items per page")
    ] = 10,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="Filter by status: open | won | lost | parked | all",
        ),
    ] = None,
    stage: Annotated[
        str | None,
        Query(
            description=(
                "Filter by stage: activation | qualification | "
                "proposal_quote | negotiation"
            )
        ),
    ] = None,
    owner_id: Annotated[
        UUID | None,
        Query(alias="ownerId", description="Filter by owning sales rep"),
    ] = None,
    customer_id: Annotated[
        UUID | None,
        Query(alias="customerId", description="Filter by customer"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search across company/contact name and product"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[DealSummary]:
    """List deals with pagination, filters, and search."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.list_deals(
        params,
        status=status_filter,
        stage=stage,
        owner_id=owner_id,
        customer_id=customer_id,
        search=search,
    )


@router.get(
    "/aggregates",
    summary="Per-stage and per-rep pipeline aggregates",
    description=(
        "Open-pipeline counts and USD-equivalent totals per stage, plus "
        "per-rep open/won/lost aggregates for the scoreboard. Richer "
        "analytics belong to the analytics backend (#45)."
    ),
    responses={200: {"description": "Aggregates"}},
)
def get_deal_aggregates(service: DealServiceDep) -> DealAggregatesResponse:
    """Trivial pipeline aggregates."""
    return service.aggregates()


@router.get(
    "/{deal_id}",
    summary="Get deal details",
    description=(
        "Full deal record with the stage-record timeline, close details "
        "and derived metrics. Detail views display the deal's native "
        "currency; USD equivalents are for list aggregation."
    ),
    responses={
        200: {"description": "Deal details"},
        404: {"description": "Deal not found"},
    },
)
def get_deal(deal_id: UUID, service: DealServiceDep) -> DealResponse:
    """Get a single deal with its timeline."""
    return service.get_deal(deal_id)


@router.put(
    "/{deal_id}",
    summary="Update a deal",
    description=(
        "Partial update of an open deal's editable fields (product, seats, "
        "value, owner). Closed and parked deals are immutable except via "
        "resume. Pass expectedVersion for optimistic locking."
    ),
    responses={
        200: {"description": "Deal updated"},
        400: {"description": "Deal is not open"},
        404: {"description": "Deal or owner not found"},
        409: {"description": "Version conflict"},
    },
)
def update_deal(
    deal_id: UUID,
    body: DealUpdate,
    service: DealServiceDep,
    expected_version: Annotated[
        int,
        Query(
            alias="expectedVersion",
            description=(
                "Current version number for optimistic locking; the request "
                "is rejected with HTTP 409 if the deal changed since load."
            ),
        ),
    ],
) -> DealResponse:
    """Update an open deal."""
    deal = service.update(deal_id, body, expected_version=expected_version)
    return service.to_response(deal)


@router.delete(
    "/{deal_id}",
    summary="Delete a deal",
    description=(
        "Permanently delete a deal and its stage record. Restricted to "
        "privileged roles (admin/manager); the deletion is audited."
    ),
    responses={
        200: {"description": "Deal deleted"},
        403: {"description": "Insufficient role"},
        404: {"description": "Deal not found"},
    },
)
def delete_deal(deal_id: UUID, service: DealServiceDep) -> DealDeleteResponse:
    """Hard-delete a deal (privileged)."""
    service.delete(deal_id)
    return DealDeleteResponse(id=deal_id)


@router.post(
    "/{deal_id}/advance",
    summary="Advance a deal to its next stage",
    description=(
        "Move an open deal exactly one stage forward (no skipping) and "
        "write the mandatory stage record. Deals at Negotiation must be "
        "closed instead."
    ),
    responses={
        200: {"description": "Deal advanced"},
        400: {"description": "Deal is not open or already at the final stage"},
        404: {"description": "Deal not found"},
        422: {"description": "A note is required to advance or close."},
    },
)
def advance_deal(
    deal_id: UUID,
    body: DealAdvanceRequest,
    service: DealServiceDep,
) -> DealResponse:
    """Advance one stage with a mandatory note."""
    deal = service.advance_stage(deal_id, body.note)
    return service.to_response(deal)


@router.post(
    "/{deal_id}/activities",
    status_code=status.HTTP_201_CREATED,
    summary="Log an activity on a deal",
    description=(
        "Append a stage record at the current stage without advancing. "
        "The note is mandatory and non-blank."
    ),
    responses={
        201: {"description": "Activity logged"},
        400: {"description": "Deal is not open"},
        404: {"description": "Deal not found"},
        422: {"description": "A note is required."},
    },
)
def log_deal_activity(
    deal_id: UUID,
    body: DealActivityCreate,
    service: DealServiceDep,
) -> DealResponse:
    """Log an activity with a mandatory note."""
    deal = service.log_activity(deal_id, body.note)
    return service.to_response(deal)


@router.post(
    "/{deal_id}/close",
    summary="Close a deal as won or lost",
    description=(
        "Close an open deal with an enumerated won/lost reason and a "
        "mandatory note. Writes the 'Closed — Won/Lost' stage record."
    ),
    responses={
        200: {"description": "Deal closed"},
        400: {"description": "Deal is not open"},
        404: {"description": "Deal not found"},
        422: {"description": "Missing note or invalid close reason"},
    },
)
def close_deal(
    deal_id: UUID,
    body: DealCloseRequest,
    service: DealServiceDep,
) -> DealResponse:
    """Close won/lost with reason + note."""
    deal = service.close(
        deal_id, result=body.result, reason=body.reason, note=body.note
    )
    return service.to_response(deal)


@router.post(
    "/{deal_id}/park",
    summary="Park a deal into the future pipeline",
    description=(
        "Freeze an open deal until a future re-engage date, recording the "
        "stage it will resume at and why now isn't the time."
    ),
    responses={
        200: {"description": "Deal parked"},
        400: {"description": "Deal is not open"},
        404: {"description": "Deal not found"},
        422: {"description": "Missing note or non-future re-engage date"},
    },
)
def park_deal(
    deal_id: UUID,
    body: DealParkRequest,
    service: DealServiceDep,
) -> DealResponse:
    """Park with a re-engage date and mandatory note."""
    deal = service.park(deal_id, parked_until=body.parked_until, note=body.note)
    return service.to_response(deal)


@router.post(
    "/{deal_id}/resume",
    summary="Resume a parked deal",
    description=(
        "Return a parked deal to the open pipeline at its recorded resume "
        "stage, clearing the re-engage date."
    ),
    responses={
        200: {"description": "Deal resumed"},
        400: {"description": "Deal is not parked"},
        404: {"description": "Deal not found"},
    },
)
def resume_deal(deal_id: UUID, service: DealServiceDep) -> DealResponse:
    """Resume a parked deal at its recorded stage."""
    deal = service.resume(deal_id)
    return service.to_response(deal)
