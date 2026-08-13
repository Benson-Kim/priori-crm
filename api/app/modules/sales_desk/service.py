"""Sales Desk analytics service (issue #45) — orchestration over the SQL.

Read-only: never flushes or commits. Under the sales-desk dependency the
session sits in ONE read-only REPEATABLE READ transaction (PostgreSQL,
mirroring the reports module), so every number inside a response — and
every row of an export — comes from the same snapshot and is internally
consistent.

Conventions:
- every monetary value is a USD equivalent per the existing FX conventions
  (``app.common.fx``), quantized to 2 dp on the aggregate;
- parked deals are excluded from ALL open aggregates (they live in the
  future pipeline);
- "this period" for the won/lost KPIs and the rep-vs-quota panel is the
  current org-local calendar QUARTER (the panel measures won value against
  the *quarterly* target owner setting);
- all date boundaries are org-local (``common/reporting_time``).
"""

import calendar
import csv
import logging
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.common.exceptions import BadRequestException
from app.common.fx import usd_equivalent
from app.common.reporting_time import reporting_date, reporting_timezone
from app.constants.enums import DealStage, DealStatus
from app.lib.config import settings
from app.modules.deals.schemas import DealFilterParams, DealOwnerResponse
from app.modules.deals.service import DealService
from app.modules.nurture.service import UPCOMING_WINDOW_DAYS
from app.modules.owner.service import (
    resolve_deal_stage_probabilities,
    resolve_rep_quarterly_targets,
)
from app.modules.sales_desk.queries import SalesDeskRepository
from app.modules.sales_desk.schemas import (
    BookingsMonth,
    ClosedPeriodKpi,
    ClosedStripColumn,
    DashboardKpis,
    DeskCompaniesResponse,
    DeskCompanyProfile,
    DeskCompanyRow,
    HygieneCounts,
    PipelineOverviewResponse,
    PipelineWeightedKpi,
    RecentCompany,
    RepQuotaLine,
    RepScoreboardCard,
    SalesDeskDashboardResponse,
    SalesDeskNotificationsResponse,
    StagePipelineLine,
    StageStripColumn,
    TeamScoreboardCard,
    TotalArrPipelineKpi,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal("0.00")
_TWO_PLACES = Decimal("0.01")

#: The desk's reporting currency: every value is converted server-side.
DESK_CURRENCY = "USD"

#: A deal is stale/cold once this many days pass with no logged activity
#: (matches the NO_ACTIVITY_30 hygiene bucket and the frontend contract).
STALE_AFTER_DAYS = 30

#: Rows of the "Recently added companies" panel (Dashboard.svg shows 4).
RECENT_COMPANIES_LIMIT = 4

#: Pipeline CSV export columns, verbatim from issue #45.
PIPELINE_EXPORT_COLUMNS = [
    "Company",
    "Contact",
    "Owner",
    "Product",
    "Seats",
    "Currency",
    "Annual value",
    "Status",
    "Stage / Parked until",
    "Days in pipeline",
    "Days idle",
    "Close reason",
    "Latest note",
]


# Org-local time helpers


def _org_day_start_utc(day: date) -> datetime:
    """UTC instant at which the org-local calendar day begins."""
    return datetime.combine(day, time.min, tzinfo=reporting_timezone()).astimezone(UTC)


def _org_local_date(instant: datetime) -> date:
    """Org-local calendar date of a stored timestamp (naive == UTC)."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(reporting_timezone()).date()


def _quarter_start(day: date) -> date:
    month = ((day.month - 1) // 3) * 3 + 1
    return date(day.year, month, 1)


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def _rolling_months(today: date) -> list[tuple[int, int]]:
    """The 12 (year, month) pairs ending with the current org-local month."""
    return [_add_months(today.year, today.month, offset) for offset in range(-11, 1)]


# CSV safety


def _csv_safe_text(value: str | None) -> str:
    """Neutralize spreadsheet formula injection in free-text CSV cells.

    The ``csv`` module already handles structural escaping (quotes, commas,
    newlines); this guards cells that spreadsheet apps would otherwise
    evaluate. Applied to user-controlled text only — server-generated
    numbers are emitted as-is.
    """
    text = value or ""
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{text}"
    return text


def write_csv_rows(rows: Iterable[list[str]], destination: str) -> int:
    """Write rows to ``destination`` as UTF-8 CSV; return the row count."""
    count = 0
    with open(destination, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


class SalesDeskService:
    """Read-only Sales Desk analytics service — never mutates state."""

    def __init__(self, db: Session, current_user=None) -> None:
        self.db = db
        self.current_user = current_user
        self.repo = SalesDeskRepository(db)

    @staticmethod
    def validate_export_size(row_count: int, export_name: str) -> None:
        """Reject an oversized synchronous CSV export (preflight and live)."""
        limit = settings.REPORT_EXPORT_MAX_ROWS
        if row_count > limit:
            raise BadRequestException(
                f"{export_name} export has more than {limit:,} rows; narrow "
                "the filters before exporting"
            )

    # Dashboard

    def get_dashboard(
        self, owner_id: uuid.UUID | None = None
    ) -> SalesDeskDashboardResponse:
        """Everything Dashboard.svg renders, from one snapshot."""
        today = reporting_date()
        probabilities = resolve_deal_stage_probabilities(self.db)
        targets, default_target = resolve_rep_quarterly_targets(self.db)

        # Open aggregates (parked excluded by the query itself)
        open_rows = self.repo.open_deal_rows(owner_id)
        weighted_total = Decimal("0")
        arr_total = Decimal("0")
        stage_totals: dict[DealStage, list] = {
            stage: [Decimal("0"), 0] for stage in DealStage
        }
        for row in open_rows:
            usd = usd_equivalent(row.value, row.currency)
            arr_total += usd
            stage = DealStage(row.stage)
            weighted_total += usd * probabilities[stage]
            stage_totals[stage][0] += usd
            stage_totals[stage][1] += 1

        pipeline_by_stage = [
            StagePipelineLine(
                stage=stage,
                stage_label=stage.label,
                value=stage_totals[stage][0].quantize(_TWO_PLACES),
                deal_count=stage_totals[stage][1],
                share=(
                    round(float(stage_totals[stage][0] / arr_total), 4)
                    if arr_total
                    else 0.0
                ),
            )
            for stage in DealStage
        ]

        # Won / lost this period (current org-local quarter)
        quarter_start = _quarter_start(today)
        next_q_year, next_q_month = _add_months(
            quarter_start.year, quarter_start.month, 3
        )
        closed_quarter = self.repo.closed_deal_rows(
            _org_day_start_utc(quarter_start),
            _org_day_start_utc(date(next_q_year, next_q_month, 1)),
            owner_id,
        )
        won_value = Decimal("0")
        lost_value = Decimal("0")
        won_count = 0
        lost_count = 0
        won_by_owner: dict[uuid.UUID, Decimal] = {}
        for row in closed_quarter:
            usd = usd_equivalent(row.value, row.currency)
            if row.status == DealStatus.WON.value:
                won_value += usd
                won_count += 1
                won_by_owner[row.owner_id] = won_by_owner.get(row.owner_id, _ZERO) + usd
            else:
                lost_value += usd
                lost_count += 1

        # Bookings — rolling 12 org-local months, real close dates
        months = _rolling_months(today)
        window_start = date(months[0][0], months[0][1], 1)
        end_year, end_month = _add_months(today.year, today.month, 1)
        closed_window = self.repo.closed_deal_rows(
            _org_day_start_utc(window_start),
            _org_day_start_utc(date(end_year, end_month, 1)),
            owner_id,
        )
        buckets: dict[tuple[int, int], list] = {
            key: [Decimal("0"), Decimal("0")] for key in months
        }
        for row in closed_window:
            closed_on = _org_local_date(row.closed_at)
            key = (closed_on.year, closed_on.month)
            if key not in buckets:  # defensive: boundary-instant drift
                continue
            usd = usd_equivalent(row.value, row.currency)
            if row.status == DealStatus.WON.value:
                buckets[key][0] += usd
            else:
                buckets[key][1] += usd
        bookings = [
            BookingsMonth(
                label=calendar.month_abbr[month],
                won_value=buckets[(year, month)][0].quantize(_TWO_PLACES),
                lost_value=buckets[(year, month)][1].quantize(_TWO_PLACES),
            )
            for year, month in months
        ]

        # Rep pipeline vs. quota: won value (this quarter) vs quarterly target
        rep_quota = []
        for owner in self.repo.deal_owners(owner_id):
            quota = targets.get(owner.id, default_target)
            rep_won = won_by_owner.get(owner.id, _ZERO).quantize(_TWO_PLACES)
            rep_quota.append(
                RepQuotaLine(
                    user=self._user_response(owner),
                    won_value=rep_won,
                    quota=quota,
                    percent=(round(float(rep_won / quota), 4) if quota else 0.0),
                )
            )

        recent_companies = [
            RecentCompany(
                id=str(row.id),
                name=(row.company_name or f"{row.first_name} {row.last_name}".strip()),
                industry=row.industry,
                contact=f"{row.first_name} {row.last_name}".strip(),
                currency=row.primary_currency or row.currency,
                created_date=_org_local_date(row.created_at),
            )
            for row in self.repo.recent_customers(owner_id, RECENT_COMPANIES_LIMIT)
        ]

        return SalesDeskDashboardResponse(
            currency=DESK_CURRENCY,
            kpis=DashboardKpis(
                pipeline_weighted=PipelineWeightedKpi(
                    value=weighted_total.quantize(_TWO_PLACES),
                    open_deal_count=len(open_rows),
                ),
                total_arr_pipeline=TotalArrPipelineKpi(
                    value=arr_total.quantize(_TWO_PLACES)
                ),
                won_this_period=ClosedPeriodKpi(
                    value=won_value.quantize(_TWO_PLACES), deal_count=won_count
                ),
                lost_this_period=ClosedPeriodKpi(
                    value=lost_value.quantize(_TWO_PLACES), deal_count=lost_count
                ),
            ),
            bookings_12_months=bookings,
            pipeline_by_stage=pipeline_by_stage,
            rep_quota=rep_quota,
            recent_companies=recent_companies,
        )

    # Notifications

    def get_notifications(
        self, owner_id: uuid.UUID | None = None
    ) -> SalesDeskNotificationsResponse:
        """Bell total + grouped counts; total is ALWAYS the sum of groups."""
        today = reporting_date()
        upcoming_cutoff = today + timedelta(days=UPCOMING_WINDOW_DAYS)

        companies_unsynced = self.repo.unsynced_companies_count(owner_id)
        due_nurture, upcoming_nurture = self.repo.nurture_due_counts(
            today, upcoming_cutoff, owner_id
        )
        due_parked, upcoming_parked = self.repo.parked_due_counts(
            today, upcoming_cutoff, owner_id
        )
        stale_deals = self.repo.stale_open_deals_count(
            today - timedelta(days=STALE_AFTER_DAYS), owner_id
        )

        future_pipeline_due = due_nurture + due_parked
        upcoming = upcoming_nurture + upcoming_parked
        return SalesDeskNotificationsResponse(
            total=companies_unsynced + future_pipeline_due + upcoming + stale_deals,
            companies_unsynced=companies_unsynced,
            future_pipeline_due=future_pipeline_due,
            upcoming=upcoming,
            stale_deals=stale_deals,
        )

    # Pipeline workspace aggregates (App.svg)

    def get_pipeline_overview(
        self, owner_id: uuid.UUID | None = None
    ) -> PipelineOverviewResponse:
        """Rep scoreboard, stage strip, hygiene counts, open total."""
        today = reporting_date()

        scoped_open = self.repo.open_deal_rows(owner_id)
        team_open = scoped_open if owner_id is None else self.repo.open_deal_rows(None)

        # Rep cards + team card are never owner-scoped: they are the selector.
        open_by_owner: dict[uuid.UUID, list] = {}
        for row in team_open:
            entry = open_by_owner.setdefault(row.owner_id, [0, Decimal("0"), 0])
            entry[0] += 1
            entry[1] += usd_equivalent(row.value, row.currency)
            if self._idle_days(row, today) > STALE_AFTER_DAYS:
                entry[2] += 1

        closed_all = self._closed_counts(None)
        reps = []
        for owner in self.repo.deal_owners(None):
            open_count, open_value, cold_count = open_by_owner.get(
                owner.id, (0, Decimal("0"), 0)
            )
            won, lost = closed_all.get(owner.id, (0, 0))
            reps.append(
                RepScoreboardCard(
                    user=self._user_response(owner),
                    open_count=open_count,
                    open_value=open_value.quantize(_TWO_PLACES),
                    win_rate=self._win_rate(won, lost),
                    cold_count=cold_count,
                )
            )

        team_won = sum(won for won, _ in closed_all.values())
        team_lost = sum(lost for _, lost in closed_all.values())
        team = TeamScoreboardCard(
            open_count=len(team_open),
            open_value=sum(
                (usd_equivalent(row.value, row.currency) for row in team_open),
                Decimal("0"),
            ).quantize(_TWO_PLACES),
            win_rate=self._win_rate(team_won, team_lost),
            cold_count=sum(
                1 for row in team_open if self._idle_days(row, today) > STALE_AFTER_DAYS
            ),
        )

        # Stage strip + closed column + hygiene + open total: owner-scoped.
        stage_totals: dict[DealStage, list] = {
            stage: [0, Decimal("0"), 0] for stage in DealStage
        }
        open_total = Decimal("0")
        active_week = quiet_8_30 = no_activity_30 = open_45 = 0
        for row in scoped_open:
            usd = usd_equivalent(row.value, row.currency)
            open_total += usd
            entry = stage_totals[DealStage(row.stage)]
            entry[0] += 1
            entry[1] += usd
            entry[2] += self._age_days(row, today)

            idle = self._idle_days(row, today)
            if idle <= 7:
                active_week += 1
            elif idle <= STALE_AFTER_DAYS:
                quiet_8_30 += 1
            else:
                no_activity_30 += 1
            if self._age_days(row, today) > 45:
                open_45 += 1

        stages = [
            StageStripColumn(
                stage=stage,
                stage_label=stage.label,
                count=stage_totals[stage][0],
                value=stage_totals[stage][1].quantize(_TWO_PLACES),
                avg_days_in_stage=(
                    round(stage_totals[stage][2] / stage_totals[stage][0])
                    if stage_totals[stage][0]
                    else 0
                ),
            )
            for stage in DealStage
        ]

        closed_scoped = self._closed_counts(owner_id)
        won_count = sum(won for won, _ in closed_scoped.values())
        lost_count = sum(lost for _, lost in closed_scoped.values())

        return PipelineOverviewResponse(
            currency=DESK_CURRENCY,
            reps=reps,
            team=team,
            stages=stages,
            closed=ClosedStripColumn(
                count=won_count + lost_count,
                win_rate=self._win_rate(won_count, lost_count),
                won_count=won_count,
                lost_count=lost_count,
            ),
            hygiene=HygieneCounts(
                all=self.repo.total_deal_count(owner_id),
                active_week=active_week,
                quiet_8_30=quiet_8_30,
                no_activity_30=no_activity_30,
                open_45=open_45,
            ),
            open_pipeline_total=open_total.quantize(_TWO_PLACES),
        )

    # CSV exports

    def count_pipeline_export_rows(self, filters: DealFilterParams) -> int:
        """Exact preflight row count for the pipeline export."""
        return self._deal_service().pipeline_query(filters).count()

    def iter_pipeline_export_rows(
        self, filters: DealFilterParams, *, batch_size: int
    ) -> Iterator[list[str]]:
        """Yield pipeline CSV data rows honouring the active list filters.

        Pages through the SAME filtered + ordered query as the pipeline
        list; under the REPEATABLE READ snapshot the pages are mutually
        consistent.
        """
        today = reporting_date()
        query = self._deal_service().pipeline_query(filters)
        offset = 0
        while True:
            batch = query.offset(offset).limit(batch_size).all()
            for deal in batch:
                yield self._pipeline_export_row(deal, today)
            if len(batch) < batch_size:
                return
            offset += batch_size

    @staticmethod
    def _pipeline_export_row(deal, today: date) -> list[str]:
        activities = sorted(
            deal.activities, key=lambda a: (a.activity_date, a.created_at)
        )
        if activities:
            first_date = activities[0].activity_date
            last_date = activities[-1].activity_date
        else:  # defensive: every service-created deal has a first record
            first_date = last_date = _org_local_date(deal.created_at)

        if deal.status == DealStatus.PARKED.value and deal.parked_until:
            stage_or_parked = f"Parked until {deal.parked_until.isoformat()}"
        else:
            stage_or_parked = DealStage(deal.stage).label

        owner_name = f"{deal.owner.first_name} {deal.owner.last_name}".strip()
        return [
            _csv_safe_text(deal.customer.display_name),
            _csv_safe_text(deal.customer.full_name),
            _csv_safe_text(owner_name),
            _csv_safe_text(deal.product),
            str(deal.seats),
            deal.currency,
            str(deal.value),
            deal.status,
            _csv_safe_text(stage_or_parked),
            str((today - first_date).days),
            str((today - last_date).days),
            _csv_safe_text(deal.close_reason or ""),
            _csv_safe_text(activities[-1].note if activities else ""),
        ]

    def dashboard_export_rows(self, owner_id: uuid.UUID | None = None) -> list[list]:
        """The dashboard payload flattened into sectioned CSV rows."""
        dashboard = self.get_dashboard(owner_id)
        kpis = dashboard.kpis
        rows: list[list] = [
            ["Sales Desk dashboard export"],
            ["Generated (org-local)", reporting_date().isoformat()],
            ["Owner filter", str(owner_id) if owner_id else "All owners"],
            ["Currency", dashboard.currency],
            [],
            ["KPIs"],
            ["Metric", "Value (USD)", "Deals"],
            [
                "Pipeline (weighted)",
                str(kpis.pipeline_weighted.value),
                str(kpis.pipeline_weighted.open_deal_count),
            ],
            ["Total ARR pipeline", str(kpis.total_arr_pipeline.value), ""],
            [
                "Won this period",
                str(kpis.won_this_period.value),
                str(kpis.won_this_period.deal_count),
            ],
            [
                "Lost this period",
                str(kpis.lost_this_period.value),
                str(kpis.lost_this_period.deal_count),
            ],
            [],
            ["Bookings — 12 months"],
            ["Month", "Won (USD)", "Lost (USD)"],
        ]
        rows.extend(
            [month.label, str(month.won_value), str(month.lost_value)]
            for month in dashboard.bookings_12_months
        )
        rows.extend([[], ["Active pipeline by stage"]])
        rows.append(["Stage", "Value (USD)", "Deals", "Share"])
        rows.extend(
            [
                _csv_safe_text(line.stage_label),
                str(line.value),
                str(line.deal_count),
                str(line.share),
            ]
            for line in dashboard.pipeline_by_stage
        )
        rows.extend([[], ["Rep pipeline vs. quota"]])
        rows.append(["Rep", "Won (USD)", "Quota (USD)", "Percent"])
        rows.extend(
            [
                _csv_safe_text(line.user.name),
                str(line.won_value),
                str(line.quota),
                str(line.percent),
            ]
            for line in dashboard.rep_quota
        )
        rows.extend([[], ["Recently added companies"]])
        rows.append(["Name", "Industry", "Contact", "Currency", "Created"])
        rows.extend(
            [
                _csv_safe_text(company.name),
                _csv_safe_text(company.industry or ""),
                _csv_safe_text(company.contact),
                company.currency,
                company.created_date.isoformat(),
            ]
            for company in dashboard.recent_companies
        )
        return rows

    # Internals

    def _deal_service(self) -> DealService:
        """DealService on the same session: the export reads the exact

        filtered + ordered query the pipeline list serves.
        """
        return DealService(self.db, current_user=self.current_user)

    def _closed_counts(
        self, owner_id: uuid.UUID | None
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """All-time (won, lost) counts per owner."""
        counts: dict[uuid.UUID, tuple[int, int]] = {}
        for row_owner, status, count in self.repo.closed_counts_by_owner(owner_id):
            won, lost = counts.get(row_owner, (0, 0))
            if status == DealStatus.WON.value:
                won += count
            else:
                lost += count
            counts[row_owner] = (won, lost)
        return counts

    @staticmethod
    def _win_rate(won: int, lost: int) -> float | None:
        closed = won + lost
        if closed == 0:
            return None
        return round(won / closed, 4)

    @staticmethod
    def _age_days(row, today: date) -> int:
        first = row.first_activity or _org_local_date(row.created_at)
        return (today - first).days

    @staticmethod
    def _idle_days(row, today: date) -> int:
        last = row.last_activity or _org_local_date(row.created_at)
        return (today - last).days

    # Companies directory (Companies.svg, #46)

    def get_companies(
        self,
        owner_id: uuid.UUID | None = None,
        search: str | None = None,
        unsynced_only: bool = False,
        limit: int = 200,
    ) -> DeskCompaniesResponse:
        """The companies directory with profiles, owner and deal counts.

        Profiles, owners and deal counts are each fetched in one batched
        query keyed by the page's customer ids, so the row count does not
        drive the query count.
        """
        rows, total = self.repo.desk_companies(owner_id, search, unsynced_only, limit)
        customer_ids = [row.id for row in rows]

        profiles_by_customer: dict[uuid.UUID, list] = {}
        for profile in self.repo.billing_profiles_for(customer_ids):
            profiles_by_customer.setdefault(profile.customer_id, []).append(profile)

        owners = {
            user.id: self._user_response(user)
            for user in self.repo.users_by_id(
                [row.owner_id for row in rows if row.owner_id]
            )
        }

        open_counts: dict[uuid.UUID, int] = {}
        closed_counts: dict[uuid.UUID, int] = {}
        total_counts: dict[uuid.UUID, int] = {}
        for customer_id, status, count in self.repo.deal_counts_for(customer_ids):
            total_counts[customer_id] = total_counts.get(customer_id, 0) + count
            if status == DealStatus.OPEN.value:
                open_counts[customer_id] = open_counts.get(customer_id, 0) + count
            elif status in (DealStatus.WON.value, DealStatus.LOST.value):
                closed_counts[customer_id] = closed_counts.get(customer_id, 0) + count

        items = []
        for row in rows:
            default_currency = row.primary_currency or row.currency
            profiles = profiles_by_customer.get(row.id, [])
            # Primary currency leads, matching the order the table renders.
            profiles.sort(key=lambda profile: profile.currency != default_currency)

            items.append(
                DeskCompanyRow(
                    id=str(row.id),
                    name=(
                        row.company_name or f"{row.first_name} {row.last_name}".strip()
                    ),
                    industry=row.industry,
                    contact=f"{row.first_name} {row.last_name}".strip(),
                    email=row.email,
                    phone=row.phone,
                    tenant=row.tenant_domain,
                    primary_currency=default_currency,
                    registered_on=_org_local_date(row.created_at),
                    owner=owners.get(row.owner_id) if row.owner_id else None,
                    profiles=[
                        DeskCompanyProfile(
                            currency=profile.currency,
                            code=profile.code,
                            payment_terms=profile.payment_terms,
                            tax_treatment=profile.tax_treatment,
                            credit_limit=profile.credit_limit,
                            synced=profile.synced,
                            is_default=profile.currency == default_currency,
                        )
                        for profile in profiles
                    ],
                    needs_sync=any(not profile.synced for profile in profiles),
                    open_deal_count=open_counts.get(row.id, 0),
                    closed_deal_count=closed_counts.get(row.id, 0),
                    total_deal_count=total_counts.get(row.id, 0),
                )
            )

        return DeskCompaniesResponse(items=items, total=total)

    @staticmethod
    def _user_response(row) -> DealOwnerResponse:
        """Owner exactly as the deal list renders it (same shape, #39)."""
        name = f"{row.first_name} {row.last_name}".strip()
        first_initial = (row.first_name or " ")[0]
        last_initial = (row.last_name or " ")[0]
        return DealOwnerResponse(
            id=row.id,
            name=name,
            initials=f"{first_initial}{last_initial}".strip().upper(),
        )
