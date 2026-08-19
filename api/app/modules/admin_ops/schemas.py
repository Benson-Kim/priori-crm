"""Pydantic schemas for the ABAC/session-risk ops surface (issue #85).

Operator-facing DTOs over the existing primitives — ``user_sessions``,
``user_behavior_baselines`` and the append-only ``audit_events`` trail.
Read models expose exactly what an incident responder needs (risk score
and floor, status, last context, trust freshness); nothing here is a new
state store.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

#: Bound on the operator-supplied termination reason. The session row's
#: ``termination_reason`` column clamps harder (60 chars, `_terminate`
#: owns that); the audit event carries the full text.
MAX_TERMINATION_REASON_LENGTH = 200


class AdminSessionView(BaseModel):
    """One session with its live risk state (GET /admin/users/{id}/sessions)."""

    id: uuid.UUID
    status: str
    risk_score: int
    effective_score: int
    risk_floor: int
    escalation_count: int
    termination_reason: str | None
    device_fingerprint: str | None
    last_ip: str | None
    last_country: str | None
    last_geo_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserSessionsResponse(BaseModel):
    user_id: uuid.UUID
    sessions: list[AdminSessionView]


class AdminSessionTerminateRequest(BaseModel):
    """Optional operator-supplied reason for the audited terminate."""

    reason: str | None = Field(None, max_length=MAX_TERMINATION_REASON_LENGTH)


class AdminSessionTerminateResponse(BaseModel):
    id: uuid.UUID
    status: str
    termination_reason: str | None

    model_config = {"from_attributes": True}


class AdminBaselineEntry(BaseModel):
    """One absorbed trust entry with its verification freshness."""

    value: str
    verified_at: datetime | None
    fresh: bool


class AdminVolumeBaseline(BaseModel):
    """Learned per-window volume statistics for one sensitivity class."""

    ewma: float
    windows: int
    count: int
    window_started_at: datetime | None


class AdminUserBaselineResponse(BaseModel):
    """GET /admin/users/{id}/baseline — the learned behavioural baseline.

    ``learned`` is False when the user has no baseline row yet (nothing
    absorbed, nothing learned); every collection is then empty.
    """

    user_id: uuid.UUID
    learned: bool
    devices: list[AdminBaselineEntry]
    countries: list[AdminBaselineEntry]
    hour_counts: dict[str, int]
    hour_observations: int
    volume_baselines: dict[str, AdminVolumeBaseline]
    updated_at: datetime | None


class AdminBaselineEntryExpiryResponse(BaseModel):
    """Result of the audited expiry of one absorbed entry."""

    user_id: uuid.UUID
    kind: str
    value: str
    expired: bool


class AdminRiskEventView(BaseModel):
    """One session risk event from the append-only audit trail."""

    id: uuid.UUID
    created_at: datetime
    action: str
    session_id: uuid.UUID
    user_id: uuid.UUID | None
    detail: dict | None


class AdminRiskEventsResponse(BaseModel):
    """GET /admin/risk-events — paginated, with honest coverage.

    ``total`` counts every row matching the filters, so a truncated page
    is never mistaken for the full picture.
    """

    events: list[AdminRiskEventView]
    total: int
    limit: int
    offset: int
