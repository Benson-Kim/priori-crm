"""Pydantic schemas for accounting periods."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PeriodCreate(BaseModel):
    """Create an accounting period (initially open)."""

    start_date: date = Field(..., alias="startDate")
    end_date: date = Field(..., alias="endDate")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_range(self) -> "PeriodCreate":
        """The window must be non-empty."""
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class PeriodCloseRequest(BaseModel):
    """Optional note recorded in the audit trail when closing a period."""

    note: str | None = Field(None, max_length=500)


class PeriodReopenRequest(BaseModel):
    """Reopening always requires a justification (audited)."""

    reason: str = Field(..., min_length=3, max_length=500)


class PeriodResponse(BaseModel):
    """Accounting period as returned by the API."""

    id: UUID
    start_date: date
    end_date: date
    status: str
    closed_at: datetime | None = None
    closed_by: UUID | None = None
    reopen_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
