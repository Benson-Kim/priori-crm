"""Future pipeline API endpoints (Sales Desk nurture list, issue #40)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.common.dependencies import NurtureServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
from app.modules.deals.schemas import DealResponse
from app.modules.nurture.schemas import (
    NurtureEngageRequest,
    NurtureNotificationCounts,
    NurtureProspectCreate,
    NurtureProspectResponse,
    NurtureSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    response_model=NurtureProspectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a planned prospect",
    description=(
        "Add a company to the future pipeline nurture list ('+ Add planned "
        "deal'): company (required), contact, owner, engage-by date "
        "(defaults to +90 days server-side when omitted), est. annual "
        "value and note."
    ),
    responses={
        201: {"description": "Prospect created successfully"},
        404: {"description": "Owner not found"},
    },
)
def create_prospect(
    body: NurtureProspectCreate, service: NurtureServiceDep
) -> NurtureProspectResponse:
    """Create a nurture prospect."""
    prospect = service.create(body, user_id=service.actor_id)
    return service.build_response(prospect)


@router.get(
    "",
    response_model=PaginatedResponse[NurtureProspectResponse],
    summary="List nurture prospects",
    description=(
        "Paginated nurture list, soonest-due first, with search (company, "
        "contact) and owner filters. Every due badge (due_state: overdue / "
        "today / in_days: N) and status dot (dot_state) is computed "
        "server-side against the org-local date boundary — the UI never "
        "does date math."
    ),
    responses={200: {"description": "List of nurture prospects"}},
)
def list_prospects(
    service: NurtureServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    owner_id: Annotated[
        UUID | None,
        Query(description="Filter by prospect owner (sales rep)", alias="ownerId"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search company or contact"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[NurtureProspectResponse]:
    """List nurture prospects with pagination and filters."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.list_prospects(params, search=search, owner_id=owner_id)


@router.get(
    "/summary",
    response_model=NurtureSummary,
    summary="Get nurture list header aggregates",
    description=(
        "Count, total est. ARR and due-now count over the same filtered "
        "set as the list ('Nurture list — 4 companies · est. $67,020 "
        "potential ARR', '1 due now')."
    ),
    responses={200: {"description": "Nurture list aggregates"}},
)
def get_nurture_summary(
    service: NurtureServiceDep,
    owner_id: Annotated[
        UUID | None,
        Query(description="Restrict aggregates to one owner", alias="ownerId"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search company or contact"),
    ] = None,
) -> NurtureSummary:
    """Get header aggregates for the nurture list."""
    return service.get_summary(search=search, owner_id=owner_id)


@router.get(
    "/counts",
    response_model=NurtureNotificationCounts,
    summary="Get future pipeline due counts",
    description=(
        "Notification counts for the sidebar badge / bell (#45): due "
        "nurture (engage_on <= today), upcoming nurture (due within 14 "
        "days) and due parked deals (parked_until <= today), all against "
        "the org-local date boundary."
    ),
    responses={200: {"description": "Due counts"}},
)
def get_nurture_counts(service: NurtureServiceDep) -> NurtureNotificationCounts:
    """Get the due/upcoming notification counts."""
    return service.get_notification_counts()


@router.get(
    "/{prospect_id}",
    response_model=NurtureProspectResponse,
    summary="Get nurture prospect details",
    responses={
        200: {"description": "Prospect details"},
        404: {"description": "Prospect not found"},
    },
)
def get_prospect(
    prospect_id: UUID, service: NurtureServiceDep
) -> NurtureProspectResponse:
    """Get a nurture prospect by ID."""
    return service.build_response(service.get_by_id(prospect_id))


@router.delete(
    "/{prospect_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete nurture prospect",
    description="Remove a prospect from the nurture list without engaging it.",
    responses={
        204: {"description": "Prospect deleted successfully"},
        404: {"description": "Prospect not found"},
    },
)
def delete_prospect(prospect_id: UUID, service: NurtureServiceDep) -> None:
    """Delete a nurture prospect."""
    service.delete(prospect_id)


@router.post(
    "/{prospect_id}/engage",
    response_model=DealResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Engage a prospect ('Start engaging →')",
    description=(
        "Atomically create a customer (via the existing customers service, "
        "reusing its validation and case-insensitive email dedup) plus an "
        "open deal at stage Activation, then remove the prospect. On a "
        "duplicate email the 409 response carries the existing customer's "
        "id in details.customer_id — re-submit with customer_id to link "
        "the deal to that customer instead. No orphan customer is left "
        "behind if deal creation fails."
    ),
    responses={
        201: {"description": "Customer + deal created, prospect removed"},
        400: {
            "description": (
                "No owner/value to engage with, or the deal currency has "
                "no billing profile"
            )
        },
        404: {"description": "Prospect, linked customer or owner not found"},
        409: {
            "description": (
                "Duplicate customer email — details.customer_id offers the "
                "re-submit-linking option"
            )
        },
    },
)
def engage_prospect(
    prospect_id: UUID, body: NurtureEngageRequest, service: NurtureServiceDep
) -> DealResponse:
    """Engage a prospect into a customer + open deal at Activation."""
    deal = service.engage(prospect_id, body)
    return service.build_deal_response(deal)
