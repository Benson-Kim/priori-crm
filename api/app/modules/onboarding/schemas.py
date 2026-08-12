"""Pydantic schemas for the Sales Desk onboarding API.

Response field names are the frontend's screen contract (issue #41 /
Onboarding.svg): the UI renders ``plan_label`` ("{product} · {seats}
seats"), the server-computed ``percent_complete`` and the
``tasks_completed`` / ``tasks_total`` ("N of 7 tasks complete") counts
verbatim and never computes them. Every mutation returns the full updated
record so the UI's optimistic update (#48) can reconcile.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class OnboardingTaskResponse(BaseModel):
    """One ordered checklist step exactly as the screen renders it."""

    position: int = Field(description="1-based step order ('Step N')")
    label: str
    done: bool
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class OnboardingResponse(BaseModel):
    """An onboarding checklist exactly as the Onboarding screen consumes it.

    ``percent_complete`` and the ``tasks_completed`` / ``tasks_total``
    counts are server-computed on every read AND every mutation — the UI
    never derives them.
    """

    id: UUID
    deal_id: UUID
    customer_id: UUID
    company_name: str
    plan_label: str = Field(
        description="Plan line captured at creation: '{product} · {seats} seats'"
    )

    percent_complete: int = Field(
        description="Server-computed completion percent (e.g. 43, 86)"
    )
    tasks_completed: int = Field(
        description="Number of done steps (the N in 'N of 7 tasks complete')"
    )
    tasks_total: int = Field(
        description="Total steps on this checklist (the 7 in 'N of 7')"
    )

    steps: list[OnboardingTaskResponse] = Field(
        description="Ordered steps: {position, label, done, completed_at}"
    )

    template_version: int = Field(
        description=(
            "Owner template version this checklist was created from "
            "(template versioning: later template edits never mutate it)"
        )
    )
    is_complete: bool
    completed_at: datetime | None

    created_at: datetime
    updated_at: datetime
