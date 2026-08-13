"""Pydantic schemas for the Sales Desk analytics module (issue #45).

The dashboard payload maps 1:1 to the panels of the FINAL design
(``docs/sales-desk-designs/Dashboard.svg``, branch ``sales-desk-designs``):
KPI row, Bookings — 12 months, Active pipeline by stage, Rep pipeline vs.
quota, Recently added companies. The older prototype panels (funnel
conversions, currency exposure, close-reason bars, hygiene panel,
largest-open/needs-attention tables) are deliberately NOT modelled here.

Conventions (matching the consuming screens):
- flat snake_case objects;
- every monetary value is a server-converted USD equivalent
  (``app.common.fx`` conventions), 2 dp;
- ratio fields (``share``, ``percent``, ``win_rate``) are 0..1 fractions
  driving bar widths; ``percent`` may exceed 1 when a rep beats quota.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.constants.enums import DealStage
from app.modules.deals.schemas import DealOwnerResponse

# Dashboard (Dashboard.svg)


class PipelineWeightedKpi(BaseModel):
    """PIPELINE (WEIGHTED): sum of open value x stage probability + open count."""

    value: Decimal = Field(description="Weighted open pipeline, USD equivalent")
    open_deal_count: int = Field(description="Open deals (parked excluded)")


class TotalArrPipelineKpi(BaseModel):
    """TOTAL ARR PIPELINE: unweighted sum of open deal values."""

    value: Decimal = Field(description="Unweighted open pipeline, USD equivalent")


class ClosedPeriodKpi(BaseModel):
    """WON/LOST THIS PERIOD: closed value + count for the current quarter."""

    value: Decimal = Field(description="Closed value this period, USD equivalent")
    deal_count: int = Field(description="Deals closed this period")


class DashboardKpis(BaseModel):
    """The four KPI cards across the top of the dashboard."""

    pipeline_weighted: PipelineWeightedKpi
    total_arr_pipeline: TotalArrPipelineKpi
    won_this_period: ClosedPeriodKpi
    lost_this_period: ClosedPeriodKpi


class BookingsMonth(BaseModel):
    """One month of the Bookings — 12 months chart (rolling window)."""

    label: str = Field(description="Month label as rendered (e.g. 'Sep')")
    won_value: Decimal = Field(description="Won value closed this month, USD")
    lost_value: Decimal = Field(description="Lost value closed this month, USD")


class StagePipelineLine(BaseModel):
    """One row of Active pipeline by stage (open stages only)."""

    stage: DealStage
    stage_label: str = Field(description="Display label (design vocabulary)")
    value: Decimal = Field(description="Open value at this stage, USD equivalent")
    deal_count: int
    share: float = Field(
        description="Share of the total open pipeline, 0..1 (drives bar width)"
    )


class RepQuotaLine(BaseModel):
    """One row of Rep pipeline vs. quota: won value against quarterly target."""

    user: DealOwnerResponse
    won_value: Decimal = Field(
        description="Value won by this rep in the current quarter, USD equivalent"
    )
    quota: Decimal = Field(
        description="Quarterly won-value target (owner setting), USD"
    )
    percent: float = Field(
        description=(
            "won_value as a share of quota, 0..1 (drives bar width; may "
            "exceed 1). 0 when the quota is 0."
        )
    )


class RecentCompany(BaseModel):
    """One row of Recently added companies (newest first)."""

    id: str = Field(description="Customer id")
    name: str
    industry: str | None
    contact: str = Field(description="Contact person full name")
    currency: str = Field(description="The company's primary billing currency")
    created_date: date = Field(description="Org-local date the company was added")


class SalesDeskDashboardResponse(BaseModel):
    """Everything Dashboard.svg renders, computed under ONE read snapshot."""

    currency: str = Field(description="Reporting currency (always USD)")
    kpis: DashboardKpis
    bookings_12_months: list[BookingsMonth] = Field(
        description="Rolling 12 org-local months ending with the current month"
    )
    pipeline_by_stage: list[StagePipelineLine]
    rep_quota: list[RepQuotaLine]
    recent_companies: list[RecentCompany]


# Notifications (bell + sidebar badges, #52)


class SalesDeskNotificationsResponse(BaseModel):
    """Bell badge total + the grouped counts feeding the sidebar badges.

    ``total`` is ALWAYS the sum of the four groups — the single source of
    truth for the shared-chrome badges.
    """

    total: int = Field(description="Bell badge: sum of the four groups below")
    companies_unsynced: int = Field(
        description="Companies with at least one unsynced billing profile (#43)"
    )
    future_pipeline_due: int = Field(
        description="Due nurture prospects + due parked deals (#40)"
    )
    upcoming: int = Field(
        description=(
            "Nurture prospects and parked deals due within the next 14 days "
            "(exclusive of today — those are already due)"
        )
    )
    stale_deals: int = Field(
        description="Open deals with no activity for more than 30 days"
    )


# Pipeline workspace aggregates (App.svg, not landed with #39)


class RepScoreboardCard(BaseModel):
    """One rep card of the pipeline scoreboard."""

    user: DealOwnerResponse
    open_count: int = Field(description="Open deals (parked excluded)")
    open_value: Decimal = Field(description="Open value, USD equivalent")
    win_rate: float | None = Field(
        description="Won share of closed deals, 0..1; null when nothing closed"
    )
    cold_count: int = Field(
        description="Open deals with no activity for more than 30 days"
    )


class TeamScoreboardCard(BaseModel):
    """The 'Whole team' card (never owner-scoped)."""

    open_count: int
    open_value: Decimal
    win_rate: float | None
    cold_count: int


class StageStripColumn(BaseModel):
    """One open-stage column of the stage summary strip."""

    stage: DealStage
    stage_label: str
    count: int
    value: Decimal = Field(description="Open value at this stage, USD equivalent")
    avg_days_in_stage: int = Field(
        description="Mean days-in-pipeline of the open deals at this stage"
    )


class ClosedStripColumn(BaseModel):
    """The CLOSED column of the stage strip (counts, not totals)."""

    count: int
    win_rate: float | None = Field(
        description="Won share of closed deals, 0..1; null when nothing closed"
    )
    won_count: int
    lost_count: int


class HygieneCounts(BaseModel):
    """Live counts behind the activity filter chips (list-filter semantics)."""

    all: int = Field(description="Every deal in scope, any status")
    active_week: int = Field(description="Open, last activity within 7 days")
    quiet_8_30: int = Field(description="Open, last activity 8-30 days ago")
    no_activity_30: int = Field(description="Open, no activity for 30+ days")
    open_45: int = Field(description="Open, in the pipeline for more than 45 days")


class PipelineOverviewResponse(BaseModel):
    """Pipeline workspace aggregates (App.svg) under one read snapshot.

    Rep cards and the team card are never owner-scoped (they ARE the
    selector); the stage strip, closed column, hygiene counts and the
    open-pipeline total honour the ``owner`` filter.
    """

    currency: str = Field(description="Reporting currency (always USD)")
    reps: list[RepScoreboardCard]
    team: TeamScoreboardCard
    stages: list[StageStripColumn]
    closed: ClosedStripColumn
    hygiene: HygieneCounts
    open_pipeline_total: Decimal = Field(
        description="Unweighted sum of open deal values in scope, USD equivalent"
    )


# Companies directory (Companies.svg, #46)


class DeskCompanyProfile(BaseModel):
    """One currency's billing profile as the Companies table renders it.

    ``synced`` is the internal posting flag toward accounting (ADR 0010): any
    edit flips it back to false until the profile is pushed again.
    """

    currency: str = Field(description="Billing currency of this profile")
    code: str = Field(description="Profile code, slug + sequence (#43)")
    payment_terms: str
    tax_treatment: str
    credit_limit: Decimal = Field(description="Ceiling in this profile's currency")
    synced: bool = Field(description="Pushed to accounting")
    is_default: bool = Field(description="The company's primary transacting currency")


class DeskCompanyRow(BaseModel):
    """One row of the Sales Desk companies directory.

    The customers list endpoint returns a lean summary built for the
    accounting screens; the desk table additionally shows industry, the
    contact, both billing profiles with their sync state, the owning rep and
    the company's deal counts, which is why this row exists separately.
    """

    id: str = Field(description="Customer id")
    name: str
    industry: str | None
    contact: str = Field(description="Contact person full name")
    email: str
    phone: str
    tenant: str | None = Field(description="Microsoft tenant domain, when set")
    primary_currency: str
    registered_on: date = Field(description="Org-local date the company was added")
    owner: DealOwnerResponse | None
    profiles: list[DeskCompanyProfile] = Field(
        description="One entry per billing currency, primary currency first"
    )
    needs_sync: bool = Field(description="Any profile still unpushed to accounting")
    open_deal_count: int
    closed_deal_count: int
    total_deal_count: int


class DeskCompaniesResponse(BaseModel):
    """The companies directory, newest registration first."""

    items: list[DeskCompanyRow]
    total: int = Field(description="Rows matching the filters, before the limit")
