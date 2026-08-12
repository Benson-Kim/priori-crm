"""Onboarding API endpoints (Sales Desk delivery checklists).

Checklists are never created through this API — creation happens
automatically, inside the same transaction, when a deal is closed WON
(see ``DealService.close``). Every mutation returns the full updated
record with server-recomputed percent + counts so the UI's optimistic
update (#48) can reconcile.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.common.dependencies import OnboardingServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
from app.modules.onboarding.schemas import OnboardingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "",
    response_model=PaginatedResponse[OnboardingResponse],
    summary="List onboarding checklists",
    description=(
        "Paginated onboarding list (newest first) with optional search on "
        "company name, contact or plan label. Percent complete and the "
        "'N of 7 tasks complete' counts are server-computed."
    ),
    responses={
        200: {"description": "List of onboarding checklists"},
        400: {"description": "Invalid query parameters"},
    },
)
def list_onboardings(
    service: OnboardingServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    search: Annotated[
        str | None,
        Query(description="Search company name, contact or plan label"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[OnboardingResponse]:
    """List onboarding checklists with pagination and search."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.list_onboardings(params, search=search)


@router.post(
    "/{onboarding_id}/tasks/{position}/toggle",
    response_model=OnboardingResponse,
    summary="Toggle one checklist task",
    description=(
        "Flip the done flag of the step at the given 1-based position. "
        "Returns the updated record with server-recomputed percent and "
        "'N of 7' counts so an optimistic UI update can reconcile."
    ),
    responses={
        200: {"description": "Task toggled; updated record returned"},
        404: {"description": "Onboarding or task position not found"},
    },
)
def toggle_onboarding_task(
    onboarding_id: UUID,
    position: Annotated[int, Path(ge=1, description="1-based step position")],
    service: OnboardingServiceDep,
) -> OnboardingResponse:
    """Toggle a task's completion (done flag + completion timestamp)."""
    onboarding = service.toggle_task(onboarding_id, position)
    return service.build_response(onboarding)


@router.post(
    "/{onboarding_id}/complete",
    response_model=OnboardingResponse,
    summary="Mark an onboarding complete",
    description=(
        "Mark every remaining step done and stamp the checklist complete. "
        "Returns the updated record with server-recomputed percent + counts."
    ),
    responses={
        200: {"description": "Onboarding marked complete"},
        400: {"description": "Onboarding is already complete"},
        404: {"description": "Onboarding not found"},
    },
)
def complete_onboarding(
    onboarding_id: UUID, service: OnboardingServiceDep
) -> OnboardingResponse:
    """Mark an onboarding checklist complete."""
    onboarding = service.mark_complete(onboarding_id)
    return service.build_response(onboarding)
