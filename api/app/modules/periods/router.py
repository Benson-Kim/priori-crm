"""Accounting-period administration endpoints.

Reads require authentication (embedded in the service dependency);
mutations additionally require a privileged role - closing a period is a
financial control, not a routine edit.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.common.dependencies import PeriodsServiceDep, require_privileged
from app.modules.periods.schemas import (
    PeriodCloseRequest,
    PeriodCreate,
    PeriodReopenRequest,
    PeriodResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "",
    summary="List accounting periods",
    description="All accounting periods, newest first.",
)
def list_periods(service: PeriodsServiceDep) -> list[PeriodResponse]:
    return [PeriodResponse.model_validate(p) for p in service.list_periods()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create an accounting period",
    description="Create an open accounting period. Overlaps are rejected.",
    dependencies=[Depends(require_privileged())],
)
def create_period(body: PeriodCreate, service: PeriodsServiceDep) -> PeriodResponse:
    return PeriodResponse.model_validate(service.create(body))


@router.post(
    "/{period_id}/close",
    summary="Close an accounting period",
    description=(
        "Close the period: documents and payments dated inside it are "
        "rejected until it is reopened. Audited."
    ),
    dependencies=[Depends(require_privileged())],
)
def close_period(
    period_id: UUID,
    service: PeriodsServiceDep,
    body: PeriodCloseRequest | None = None,
) -> PeriodResponse:
    note = body.note if body else None
    return PeriodResponse.model_validate(service.close(period_id, note=note))


@router.post(
    "/{period_id}/reopen",
    summary="Reopen a closed accounting period",
    description="Reopen a closed period. Requires a justification; audited.",
    dependencies=[Depends(require_privileged())],
)
def reopen_period(
    period_id: UUID,
    body: PeriodReopenRequest,
    service: PeriodsServiceDep,
) -> PeriodResponse:
    return PeriodResponse.model_validate(service.reopen(period_id, body.reason))
