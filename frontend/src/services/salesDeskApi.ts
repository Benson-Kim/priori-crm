/**
 * Sales Desk API service.
 *
 * Backs the Sales Desk dashboard and pipeline. The backend module does not
 * exist yet, so every function resolves against the in-memory store in
 * `salesDeskStore.ts` (seeded from `salesDeskSeed.ts`) instead of `apiGet`.
 *
 * The signatures follow the shape the other services use
 * (`dashboardApi.ts`, `reportsApi.ts`): async, currency-parameterised, and
 * returning flat snake_case response objects. When `GET/POST /sales-desk/*`
 * ships, each body swaps to an `apiGet`/`apiPost` call and the response types
 * move to `Schema<"...">` without any caller changes.
 *
 * Amounts are returned already converted into the requested currency; the
 * fixtures hold USD, which is the desk's base currency.
 */

import {
    ANNUAL_DISCOUNT,
    BILLING_CURRENCIES,
    CURRENCY_SYMBOLS,
    DEAL_STAGES,
    FX_RATES,
    ONBOARDING_TASKS,
    PRODUCTS,
    QUOTE_CURRENCIES,
    SEED_BOOKINGS,
    SEED_REPS,
    STAGE_PROBABILITY,
    companyCodePrefix,
    type BillingCurrency,
    type DealStage,
    type DealStatus,
    type QuoteCurrency,
    type QuoteStatus,
    type SeedBillingProfile,
    type SeedCompany,
    type SeedDeal,
    type SeedOnboarding,
    type SeedProspect,
} from "./salesDeskSeed";

export {
    ANNUAL_DISCOUNT,
    BILLING_CURRENCIES,
    PRODUCTS,
    QUOTE_CURRENCIES,
    REFERENCE_CURRENCIES,
} from "./salesDeskSeed";
export type { QuoteCurrency, QuoteStatus } from "./salesDeskSeed";
import {
    addCompany,
    addDeal,
    addProspect as addProspectRecord,
    addQuote,
    allocateId,
    appendRecord,
    findCompany,
    findDeal,
    findProspect,
    getCompanies,
    subscribeToStore,
    getDeals,
    getOnboarding as getOnboardingRecords,
    getProspects as getProspectRecords,
    getQuotes as getQuoteRecords,
    moveToNurture,
    patchBillingProfile,
    patchDeal,
    patchQuote,
    removeProspect,
    setOnboardingTask as setOnboardingTaskRecord,
} from "./salesDeskStore";

export type { BillingCurrency, DealStage, DealStatus } from "./salesDeskSeed";
export {
    DEAL_STAGES,
    INDUSTRIES,
    LOST_REASONS,
    PAYMENT_TERMS,
    TAX_TREATMENTS,
    WON_REASONS,
} from "./salesDeskSeed";

export const DEFAULT_DESK_CURRENCY: BillingCurrency = "USD";

/** A deal is stale once this many days pass with no logged activity. */
export const STALE_AFTER_DAYS = 30;

// Response contracts. These mirror the shapes the future endpoints return.

/** KPI row: weighted forecast, unweighted pipeline, and closed-deal totals. */
export interface SalesDeskSummary {
    weighted_pipeline: number;
    total_pipeline: number;
    open_deal_count: number;
    won_amount: number;
    won_deal_count: number;
    lost_amount: number;
    lost_deal_count: number;
    currency: BillingCurrency;
}

/** One row of the "Active pipeline by stage" breakdown. */
export interface StagePipelineLine {
    stage: DealStage;
    amount: number;
    deal_count: number;
    /** Share of total open pipeline, 0 to 1, drives the bar width. */
    share: number;
}

/** One month of closed bookings. */
export interface BookingsPoint {
    month: string;
    won: number;
    lost: number;
}

/** One rep's weighted pipeline measured against their quarter quota. */
export interface RepQuotaLine {
    id: string;
    name: string;
    initials: string;
    color: string;
    weighted_pipeline: number;
    quarter_target: number;
    /** Weighted pipeline as a share of quota, 0 to 1. May exceed 1. */
    attainment: number;
}

/** A recently registered company, newest first. */
export interface RecentCompany {
    id: number;
    name: string;
    industry: string;
    contact: string;
    billing_currency: BillingCurrency;
    /** ISO date (yyyy-mm-dd). */
    registered_on: string;
}

/** A record in a deal's audit trail. */
export interface DealRecord {
    stage: string;
    note: string;
    /** ISO date (yyyy-mm-dd). */
    logged_on: string;
}

/** A pipeline row. Amounts are in the requested display currency. */
export interface PipelineDeal {
    id: number;
    company_id: number;
    company_name: string;
    contact: string;
    product: string;
    seats: number;
    /** Annual contract value, converted to the display currency. */
    value: number;
    /** Forecast value, annual value × stage probability. Null once closed. */
    weighted_value: number | null;
    /** Currency the deal actually bills in, independent of the display currency. */
    billing_currency: BillingCurrency;
    stage_index: number;
    /** Stage label, or "Won" / "Lost" once closed. */
    stage_label: string;
    status: DealStatus;
    close_reason: string | null;
    owner_id: string;
    owner_name: string;
    owner_initials: string;
    owner_color: string;
    /** Days since the deal was opened. */
    age_days: number;
    /** Days since the last logged record. */
    idle_days: number;
    latest_record: DealRecord;
    history: DealRecord[];
}

/** How a rep's book looks at a glance, for the pipeline's rep selector. */
export interface RepPipelineCard {
    id: string;
    name: string;
    initials: string;
    color: string;
    open_deal_count: number;
    open_value: number;
    /** Won as a share of closed deals, 0 to 1. Null when nothing has closed. */
    win_rate: number | null;
    /** Open deals with no activity for STALE_AFTER_DAYS or more. */
    cold_deal_count: number;
}

/** One column of the pipeline's stage summary strip. */
export interface StageSummaryColumn {
    stage: DealStage;
    deal_count: number;
    value: number;
    /** Mean age in days of the open deals sitting at this stage. */
    average_age_days: number;
}

/** The closed column of the stage strip, which counts rather than totals. */
export interface ClosedSummary {
    deal_count: number;
    won_count: number;
    lost_count: number;
    /** Won as a share of closed deals, 0 to 1. Null when nothing has closed. */
    win_rate: number | null;
}

/** Named activity filters over the open pipeline, with live counts. */
export type ActivityFilterKey =
    | "all"
    | "active_this_week"
    | "quiet_8_30"
    | "no_activity_30"
    | "open_45";

export interface ActivityFilter {
    key: ActivityFilterKey;
    label: string;
    count: number;
}

/** Everything the pipeline header needs in one round trip. */
export interface PipelineOverview {
    reps: RepPipelineCard[];
    team: Omit<RepPipelineCard, "id" | "initials" | "color">;
    stages: StageSummaryColumn[];
    closed: ClosedSummary;
    filters: ActivityFilter[];
    open_pipeline_value: number;
    currency: BillingCurrency;
}

/** A company's posting profile, as shown on the deal drawer. */
export interface BillingProfile {
    code: string;
    terms: string;
    tax: string;
    synced: boolean;
}

// Fixture helpers. All of this moves server-side with the real API.

const convert = (usd: number, currency: BillingCurrency) => usd * FX_RATES[currency];

/** Weighted value of an open deal: value × the probability of its stage. */
const weightedValue = (deal: SeedDeal) =>
    deal.valueUSD * (STAGE_PROBABILITY[deal.stage] ?? 0);

const isOpen = (deal: SeedDeal) => deal.status === "open";

const sum = <T,>(items: T[], value: (item: T) => number) =>
    items.reduce((total, item) => total + value(item), 0);

/** Days since the deal's first record, how long it has been in the pipeline. */
const ageDays = (deal: SeedDeal) => deal.history[0]?.daysAgo ?? 0;

/** Days since the most recent record, how long it has been sitting quiet. */
const idleDays = (deal: SeedDeal) => deal.history[deal.history.length - 1]?.daysAgo ?? 0;

/** ISO date `days` before today, in local time. */
function isoDaysAgo(days: number): string {
    const date = new Date();
    date.setDate(date.getDate() - days);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
        date.getDate()
    ).padStart(2, "0")}`;
}

const initialsOf = (name: string) =>
    name
        .split(" ")
        .map((part) => part[0])
        .join("")
        .slice(0, 2)
        .toUpperCase();

const repById = (id: string) => SEED_REPS.find((rep) => rep.id === id);

/** Won as a share of closed deals, or null when nothing has closed yet. */
function winRateOf(deals: SeedDeal[]): number | null {
    const closed = deals.filter((deal) => !isOpen(deal));
    if (closed.length === 0) return null;
    return closed.filter((deal) => deal.status === "won").length / closed.length;
}

/** Human label for a deal's position: its stage, or its closing outcome. */
function stageLabelOf(deal: SeedDeal): string {
    if (deal.status === "won") return "Won";
    if (deal.status === "lost") return "Lost";
    return DEAL_STAGES[deal.stage] ?? DEAL_STAGES[0];
}

/**
 * The furthest stage a deal actually recorded.
 *
 * A closed deal's `stage` is a sentinel past the end of the list, so it cannot
 * say how far the deal got: the seeded lost deal is stored at 4 but never
 * reached Negotiation. The history is the honest answer, and it is what the
 * progress bar must draw, otherwise a deal that died at Proposal & Quote
 * appears to have negotiated.
 */
function reachedStageIndex(deal: SeedDeal): number {
    let reached = 0;
    deal.history.forEach((record) => {
        const index = DEAL_STAGES.indexOf(record.stage as (typeof DEAL_STAGES)[number]);
        if (index > reached) reached = index;
    });
    return reached;
}

/** Index the UI should draw: the live stage while open, the furthest once closed. */
function stageIndexOf(deal: SeedDeal): number {
    return isOpen(deal) ? deal.stage : reachedStageIndex(deal);
}

function toDealRecord(record: { stage: string; note: string; daysAgo: number }): DealRecord {
    return { stage: record.stage, note: record.note, logged_on: isoDaysAgo(record.daysAgo) };
}

function toPipelineDeal(
    deal: SeedDeal,
    company: SeedCompany,
    currency: BillingCurrency
): PipelineDeal {
    const rep = repById(deal.owner);
    const history = deal.history.map(toDealRecord);

    return {
        id: deal.id,
        company_id: company.id,
        company_name: company.name,
        contact: company.contact,
        product: deal.product,
        seats: deal.seats,
        value: convert(deal.valueUSD, currency),
        weighted_value: isOpen(deal) ? convert(weightedValue(deal), currency) : null,
        billing_currency: deal.currency,
        stage_index: stageIndexOf(deal),
        stage_label: stageLabelOf(deal),
        status: deal.status,
        close_reason: deal.closeReason ?? null,
        owner_id: deal.owner,
        owner_name: rep?.name ?? deal.owner,
        owner_initials: initialsOf(rep?.name ?? deal.owner),
        owner_color: rep?.color ?? "#6b7688",
        age_days: ageDays(deal),
        idle_days: idleDays(deal),
        latest_record: history[history.length - 1],
        history,
    };
}

/** Resolve the company a deal belongs to, or throw, fixtures are complete. */
function companyOf(deal: SeedDeal): SeedCompany {
    const company = findCompany(deal.companyId);
    if (!company) throw new Error(`Company ${deal.companyId} not found`);
    return company;
}

/** Predicates behind the named activity filters, applied to open deals. */
const ACTIVITY_PREDICATES: Record<ActivityFilterKey, (deal: SeedDeal) => boolean> = {
    all: () => true,
    active_this_week: (deal) => isOpen(deal) && idleDays(deal) <= 7,
    quiet_8_30: (deal) => isOpen(deal) && idleDays(deal) > 7 && idleDays(deal) <= STALE_AFTER_DAYS,
    no_activity_30: (deal) => isOpen(deal) && idleDays(deal) > STALE_AFTER_DAYS,
    open_45: (deal) => isOpen(deal) && ageDays(deal) >= 45,
};

const ACTIVITY_LABELS: Record<ActivityFilterKey, string> = {
    all: "All deals",
    active_this_week: "Active this week",
    quiet_8_30: "8–30 days quiet",
    no_activity_30: "No activity 30d+",
    open_45: "Open 45d+",
};

/** Deals owned by `ownerId`, or every deal when no owner is given. */
const scopedTo = (ownerId?: string) =>
    ownerId ? getDeals().filter((deal) => deal.owner === ownerId) : getDeals();

// Dashboard endpoints

/** KPI cards: weighted forecast, unweighted pipeline, won and lost this period. */
export async function getSalesDeskSummary(
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<SalesDeskSummary> {
    const deals = getDeals();
    const open = deals.filter(isOpen);
    const won = deals.filter((deal) => deal.status === "won");
    const lost = deals.filter((deal) => deal.status === "lost");

    return {
        weighted_pipeline: convert(sum(open, weightedValue), currency),
        total_pipeline: convert(sum(open, (deal) => deal.valueUSD), currency),
        open_deal_count: open.length,
        won_amount: convert(sum(won, (deal) => deal.valueUSD), currency),
        won_deal_count: won.length,
        lost_amount: convert(sum(lost, (deal) => deal.valueUSD), currency),
        lost_deal_count: lost.length,
        currency,
    };
}

/** Open pipeline split by stage, in stage order, with each stage's share of the total. */
export async function getPipelineByStage(
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<StagePipelineLine[]> {
    const open = getDeals().filter(isOpen);
    const total = sum(open, (deal) => deal.valueUSD);

    return DEAL_STAGES.map((stage, index) => {
        const atStage = open.filter((deal) => deal.stage === index);
        const amount = sum(atStage, (deal) => deal.valueUSD);
        return {
            stage,
            amount: convert(amount, currency),
            deal_count: atStage.length,
            share: total ? amount / total : 0,
        };
    });
}

/** Closed bookings for the trailing 12 months, oldest first. */
export async function getBookingsTrend(
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<BookingsPoint[]> {
    return SEED_BOOKINGS.map(({ month, wonUSD, lostUSD }) => ({
        month,
        won: convert(wonUSD, currency),
        lost: convert(lostUSD, currency),
    }));
}

/** Each rep's weighted pipeline against their quarter quota. */
export async function getRepQuotaProgress(
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<RepQuotaLine[]> {
    const open = getDeals().filter(isOpen);

    return SEED_REPS.map((rep) => {
        const weighted = sum(
            open.filter((deal) => deal.owner === rep.id),
            weightedValue
        );
        return {
            id: rep.id,
            name: rep.name,
            initials: initialsOf(rep.name),
            color: rep.color,
            weighted_pipeline: convert(weighted, currency),
            quarter_target: convert(rep.quarterTargetUSD, currency),
            attainment: rep.quarterTargetUSD ? weighted / rep.quarterTargetUSD : 0,
        };
    });
}

/** Most recently registered companies, newest first. */
export async function getRecentCompanies(limit = 4): Promise<RecentCompany[]> {
    return [...getCompanies()]
        .sort((a, b) => a.registeredDaysAgo - b.registeredDaysAgo)
        .slice(0, limit)
        .map((company) => ({
            id: company.id,
            name: company.name,
            industry: company.industry,
            contact: company.contact,
            billing_currency: company.primaryCurrency,
            registered_on: isoDaysAgo(company.registeredDaysAgo),
        }));
}

// Pipeline endpoints

export interface PipelineQuery {
    /** Restrict to one rep's book. Omit for the whole team. */
    ownerId?: string;
    /** Named activity filter over open deals. Defaults to "all". */
    activity?: ActivityFilterKey;
    /** Include won and lost deals. Defaults to true. */
    includeClosed?: boolean;
    /** Free-text match over company, product and contact. */
    search?: string;
    /** Restrict to a single status, used by the compact view's tabs. */
    status?: DealStatus;
}

/**
 * Pipeline rows, ordered the way a rep reads them: open deals first by stage,
 * then won, then lost.
 */
export async function getPipelineDeals(
    query: PipelineQuery = {},
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<PipelineDeal[]> {
    const { ownerId, activity = "all", includeClosed = true, search, status } = query;
    const term = search?.trim().toLowerCase();

    // Closed deals have no activity signal, so the activity filters only ever
    // narrow the open set; a closed deal is either shown or hidden wholesale.
    const matchesActivity = (deal: SeedDeal) =>
        isOpen(deal) ? ACTIVITY_PREDICATES[activity](deal) : activity === "all";

    const rows = scopedTo(ownerId)
        .filter((deal) => (status ? deal.status === status : true))
        .filter((deal) => (includeClosed ? true : isOpen(deal)))
        .filter(matchesActivity)
        .filter((deal) => {
            if (!term) return true;
            const company = companyOf(deal);
            return `${company.name} ${deal.product} ${company.contact}`
                .toLowerCase()
                .includes(term);
        });

    // Open deals sort by stage; closed ones fall to the bottom, won above lost.
    const rank = (deal: SeedDeal) =>
        isOpen(deal) ? deal.stage : deal.status === "won" ? 10 : 11;

    return rows
        .sort((a, b) => rank(a) - rank(b))
        .map((deal) => toPipelineDeal(deal, companyOf(deal), currency));
}

/** Rep cards, stage strip, filter counts and the open-pipeline total. */
export async function getPipelineOverview(
    ownerId?: string,
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<PipelineOverview> {
    const all = getDeals();
    const scoped = scopedTo(ownerId);
    const scopedOpen = scoped.filter(isOpen);

    const reps: RepPipelineCard[] = SEED_REPS.map((rep) => {
        const mine = all.filter((deal) => deal.owner === rep.id);
        const open = mine.filter(isOpen);
        return {
            id: rep.id,
            name: rep.name,
            initials: initialsOf(rep.name),
            color: rep.color,
            open_deal_count: open.length,
            open_value: convert(sum(open, (deal) => deal.valueUSD), currency),
            win_rate: winRateOf(mine),
            cold_deal_count: open.filter((deal) => idleDays(deal) > STALE_AFTER_DAYS).length,
        };
    });

    const teamOpen = all.filter(isOpen);
    const closedScoped = scoped.filter((deal) => !isOpen(deal));

    return {
        reps,
        team: {
            name: "Whole team",
            open_deal_count: teamOpen.length,
            open_value: convert(sum(teamOpen, (deal) => deal.valueUSD), currency),
            win_rate: winRateOf(all),
            cold_deal_count: teamOpen.filter((deal) => idleDays(deal) > STALE_AFTER_DAYS).length,
        },
        stages: DEAL_STAGES.map((stage, index) => {
            const atStage = scopedOpen.filter((deal) => deal.stage === index);
            return {
                stage,
                deal_count: atStage.length,
                value: convert(sum(atStage, (deal) => deal.valueUSD), currency),
                average_age_days: atStage.length
                    ? Math.round(sum(atStage, ageDays) / atStage.length)
                    : 0,
            };
        }),
        closed: {
            deal_count: closedScoped.length,
            won_count: closedScoped.filter((deal) => deal.status === "won").length,
            lost_count: closedScoped.filter((deal) => deal.status === "lost").length,
            win_rate: winRateOf(scoped),
        },
        filters: (Object.keys(ACTIVITY_LABELS) as ActivityFilterKey[]).map((key) => ({
            key,
            label: ACTIVITY_LABELS[key],
            // "All deals" counts everything in scope; the rest are open-only.
            count:
                key === "all"
                    ? scoped.length
                    : scopedOpen.filter(ACTIVITY_PREDICATES[key]).length,
        })),
        open_pipeline_value: convert(sum(scopedOpen, (deal) => deal.valueUSD), currency),
        currency,
    };
}

/** One deal plus the billing profile it will invoice against. */
export interface DealDetail {
    deal: PipelineDeal;
    billing_profile: BillingProfile;
    /** Annual value in the deal's own billing currency, for the drawer tile. */
    billed_value: number;
    /** The next stage this deal can advance to, or null when it is last/closed. */
    next_stage: DealStage | null;
}

export async function getDealDetail(
    dealId: number,
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<DealDetail> {
    const deal = findDeal(dealId);
    if (!deal) throw new Error(`Deal ${dealId} not found`);
    const company = companyOf(deal);
    const profile: SeedBillingProfile = company.profiles[deal.currency];

    return {
        deal: toPipelineDeal(deal, company, currency),
        billing_profile: { ...profile },
        billed_value: convert(deal.valueUSD, deal.currency),
        next_stage:
            isOpen(deal) && deal.stage < DEAL_STAGES.length - 1
                ? DEAL_STAGES[deal.stage + 1]
                : null,
    };
}

// Pipeline writes
//
// Each of these mutates the in-memory store and resolves with the updated
// deal, so callers can refresh from the returned value. Against the real API
// they become POSTs to /sales-desk/deals/{id}/... with the same arguments.

/** Record what happened without moving the deal. */
export async function logDealActivity(
    dealId: number,
    note: string,
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<PipelineDeal> {
    const deal = findDeal(dealId);
    if (!deal) throw new Error(`Deal ${dealId} not found`);
    appendRecord(dealId, { stage: stageLabelOf(deal), note });
    return toPipelineDeal(deal, companyOf(deal), currency);
}

/** Move the deal to the next stage, recording why. */
export async function advanceDeal(
    dealId: number,
    note: string,
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<PipelineDeal> {
    const deal = findDeal(dealId);
    if (!deal) throw new Error(`Deal ${dealId} not found`);
    if (!isOpen(deal)) throw new Error("A closed deal cannot be advanced.");
    if (deal.stage >= DEAL_STAGES.length - 1) {
        throw new Error("This deal is already at the final stage. Close it instead.");
    }

    const nextStage = deal.stage + 1;
    patchDeal(dealId, { stage: nextStage });
    appendRecord(dealId, { stage: DEAL_STAGES[nextStage], note });
    return toPipelineDeal(deal, companyOf(deal), currency);
}

/** Close the deal as won or lost, with a reason and a closing note. */
export async function closeDeal(
    dealId: number,
    outcome: Extract<DealStatus, "won" | "lost">,
    reason: string,
    note: string,
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY
): Promise<PipelineDeal> {
    const deal = findDeal(dealId);
    if (!deal) throw new Error(`Deal ${dealId} not found`);
    if (!isOpen(deal)) throw new Error("This deal is already closed.");

    patchDeal(dealId, {
        status: outcome,
        stage: DEAL_STAGES.length,
        closeReason: reason,
    });
    appendRecord(dealId, {
        stage: outcome === "won" ? "Closed · Won" : "Closed · Lost",
        note,
    });
    return toPipelineDeal(deal, companyOf(deal), currency);
}

/**
 * Park the deal out of the active pipeline as a nurtured prospect. The deal
 * leaves the pipeline entirely, which is why this resolves with nothing.
 */
export async function moveDealToFuturePipeline(
    dealId: number,
    note: string,
    engageInDays = 90
): Promise<void> {
    moveToNurture(dealId, note, engageInDays);
}

// Company endpoints

/** A company's posting profile in one currency, as the UI consumes it. */
export interface CompanyBillingProfile {
    currency: BillingCurrency;
    code: string;
    terms: string;
    tax: string;
    /** Credit ceiling expressed in this profile's own currency. */
    credit_limit: number;
    synced: boolean;
    /** True when this is the company's default transacting currency. */
    is_default: boolean;
}

/** A row in the companies directory. */
export interface CompanyRow {
    id: number;
    name: string;
    industry: string;
    contact: string;
    email: string;
    phone: string;
    tenant: string | null;
    primary_currency: BillingCurrency;
    registered_on: string;
    owner_id: string;
    owner_name: string;
    owner_initials: string;
    owner_color: string;
    profiles: CompanyBillingProfile[];
    /** True when either profile still has to be pushed to accounting. */
    needs_sync: boolean;
    open_deal_count: number;
    closed_deal_count: number;
    total_deal_count: number;
}

/** A company's deal, trimmed to what the drawer's deal cards show. */
export interface CompanyDealSummary {
    id: number;
    product: string;
    billing_currency: BillingCurrency;
    value: number;
    stage_index: number;
    stage_label: string;
    status: DealStatus;
    close_reason: string | null;
    age_days: number;
}

export interface CompanyDetail {
    company: CompanyRow;
    deals: CompanyDealSummary[];
}

function toBillingProfile(
    company: SeedCompany,
    currency: BillingCurrency
): CompanyBillingProfile {
    const profile = company.profiles[currency];
    return {
        currency,
        code: profile.code,
        terms: profile.terms,
        tax: profile.tax,
        // Already denominated in this profile's currency, so no conversion.
        credit_limit: profile.creditLimit,
        synced: profile.synced,
        is_default: company.primaryCurrency === currency,
    };
}

function toCompanyRow(company: SeedCompany): CompanyRow {
    const rep = repById(company.owner);
    const deals = getDeals().filter((deal) => deal.companyId === company.id);
    const profiles = BILLING_CURRENCIES.map((currency) =>
        toBillingProfile(company, currency)
    );

    return {
        id: company.id,
        name: company.name,
        industry: company.industry,
        contact: company.contact,
        email: company.email,
        phone: company.phone,
        tenant: company.tenant,
        primary_currency: company.primaryCurrency,
        registered_on: isoDaysAgo(company.registeredDaysAgo),
        owner_id: company.owner,
        owner_name: rep?.name ?? company.owner,
        owner_initials: initialsOf(rep?.name ?? company.owner),
        owner_color: rep?.color ?? "#6b7688",
        profiles,
        needs_sync: profiles.some((profile) => !profile.synced),
        open_deal_count: deals.filter(isOpen).length,
        closed_deal_count: deals.filter((deal) => !isOpen(deal)).length,
        total_deal_count: deals.length,
    };
}

/** A rep, for owner pickers. Synchronous, the roster is a fixed reference list. */
export function getSalesReps(): Array<{ id: string; name: string; color: string }> {
    return SEED_REPS.map(({ id, name, color }) => ({ id, name, color }));
}

export interface CompanyQuery {
    /** Free-text match over name, industry, contact and tenant. */
    search?: string;
    /** Restrict to companies with an unsynced profile. */
    needsSyncOnly?: boolean;
}

/** The companies directory, newest registration first. */
export async function getCompanyList(query: CompanyQuery = {}): Promise<CompanyRow[]> {
    const term = query.search?.trim().toLowerCase();

    return [...getCompanies()]
        .sort((a, b) => a.registeredDaysAgo - b.registeredDaysAgo)
        .map(toCompanyRow)
        .filter((row) => (query.needsSyncOnly ? row.needs_sync : true))
        .filter((row) =>
            term
                ? `${row.name} ${row.industry} ${row.contact} ${row.tenant ?? ""}`
                      .toLowerCase()
                      .includes(term)
                : true
        );
}

/** One company plus its deals, for the drawer. */
export async function getCompanyDetail(companyId: number): Promise<CompanyDetail> {
    const company = findCompany(companyId);
    if (!company) throw new Error(`Company ${companyId} not found`);

    return {
        company: toCompanyRow(company),
        deals: getDeals()
            .filter((deal) => deal.companyId === companyId)
            .map((deal) => ({
                id: deal.id,
                product: deal.product,
                billing_currency: deal.currency,
                // In the deal's own billing currency, which is what the card
                // labels it with. Converting to the caller's currency here
                // would print a USD figure beside a KES chip.
                value: convert(deal.valueUSD, deal.currency),
                stage_index: stageIndexOf(deal),
                stage_label: stageLabelOf(deal),
                status: deal.status,
                close_reason: deal.closeReason ?? null,
                age_days: ageDays(deal),
            })),
    };
}

/** Commercial terms a user may change on a billing profile. */
export interface BillingProfilePatch {
    terms?: string;
    tax?: string;
    /** In the profile's own currency, which is how it is stored. */
    credit_limit?: number;
}

/**
 * Edit a profile's terms. Any change clears the sync flag: accounting is
 * holding the previous terms until the profile is pushed again.
 */
export async function updateBillingProfile(
    companyId: number,
    currency: BillingCurrency,
    patch: BillingProfilePatch
): Promise<CompanyRow> {
    if (patch.credit_limit !== undefined) {
        if (!Number.isFinite(patch.credit_limit) || patch.credit_limit < 0) {
            throw new Error("Credit limit must be a positive number.");
        }
    }

    const company = patchBillingProfile(companyId, currency, {
        ...(patch.terms !== undefined ? { terms: patch.terms } : {}),
        ...(patch.tax !== undefined ? { tax: patch.tax } : {}),
        ...(patch.credit_limit !== undefined
            ? { creditLimit: patch.credit_limit }
            : {}),
    });
    return toCompanyRow(company);
}

/** Push one currency profile to accounting. */
export async function syncBillingProfile(
    companyId: number,
    currency: BillingCurrency
): Promise<CompanyRow> {
    return toCompanyRow(patchBillingProfile(companyId, currency, { synced: true }));
}

/** Push both currency profiles to accounting. */
export async function syncBothProfiles(companyId: number): Promise<CompanyRow> {
    let company = findCompany(companyId);
    if (!company) throw new Error(`Company ${companyId} not found`);
    BILLING_CURRENCIES.forEach((currency) => {
        company = patchBillingProfile(companyId, currency, { synced: true });
    });
    return toCompanyRow(company);
}

/** Fields collected when registering a company. */
export interface NewCompanyInput {
    name: string;
    industry: string;
    contact: string;
    email: string;
    phone: string;
    tenant?: string;
    primaryCurrency: BillingCurrency;
    ownerId: string;
}

/**
 * A profile code is an accounting identity, so it has to be unique even when
 * two company names reduce to the same initials (Acme Logistics and Alpha
 * Logistics both give AL). Suffixes the prefix until nothing else claims it,
 * which is what the customers API does server-side.
 */
function uniqueCodePrefix(name: string): string {
    const base = companyCodePrefix(name);
    const taken = new Set(
        getCompanies().flatMap((company) =>
            BILLING_CURRENCIES.map((currency) => company.profiles[currency].code)
        )
    );
    const isFree = (candidate: string) =>
        BILLING_CURRENCIES.every((currency) => !taken.has(`${candidate}-${currency}`));

    if (isFree(base)) return base;
    for (let suffix = 2; ; suffix++) {
        const candidate = `${base}${suffix}`;
        if (isFree(candidate)) return candidate;
    }
}

/**
 * Register a company. Both profiles are created unsynced: nothing exists in
 * accounting until someone pushes it, and the Companies screen surfaces that
 * through its "Needs sync" filter.
 */
export async function createCompany(input: NewCompanyInput): Promise<CompanyRow> {
    const name = input.name.trim();
    if (!name) throw new Error("Company name is required.");
    if (!input.contact.trim()) throw new Error("A primary contact is required.");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.email.trim())) {
        throw new Error("Enter a valid email address.");
    }

    const prefix = uniqueCodePrefix(name);
    const company = addCompany({
        id: allocateId(),
        owner: input.ownerId,
        name,
        industry: input.industry,
        contact: input.contact.trim(),
        email: input.email.trim(),
        phone: input.phone.trim(),
        tenant: input.tenant?.trim() || null,
        primaryCurrency: input.primaryCurrency,
        registeredDaysAgo: 0,
        profiles: {
            USD: {
                code: `${prefix}-USD`,
                terms: "30 days",
                tax: "Zero-rated (export)",
                creditLimit: 25_000,
                synced: false,
            },
            KES: {
                code: `${prefix}-KES`,
                terms: "14 days",
                tax: "VAT 16%",
                creditLimit: 1_300_000,
                synced: false,
            },
        },
    });

    return toCompanyRow(company);
}

// Future pipeline endpoints

/** How urgent a prospect's engage-by date is. */
export type ProspectUrgency = "overdue" | "today" | "scheduled";

export interface ProspectRow {
    id: number;
    company: string;
    contact: string;
    note: string;
    /** ISO date (yyyy-mm-dd) the prospect should be picked up. */
    engage_on: string;
    /** Days until that date. Negative once it has passed. */
    days_until: number;
    urgency: ProspectUrgency;
    estimated_arr: number;
    owner_id: string;
    owner_name: string;
    owner_initials: string;
    owner_color: string;
}

export interface FuturePipelineSummary {
    prospect_count: number;
    /** Prospects whose engage-by date has arrived or passed. */
    due_count: number;
    total_estimated_arr: number;
    currency: BillingCurrency;
}

const urgencyOf = (daysUntil: number): ProspectUrgency =>
    daysUntil < 0 ? "overdue" : daysUntil === 0 ? "today" : "scheduled";

/** ISO date `days` from today, in local time. */
function isoDaysFromNow(days: number): string {
    return isoDaysAgo(-days);
}

function toProspectRow(
    prospect: SeedProspect,
    currency: BillingCurrency
): ProspectRow {
    const rep = repById(prospect.owner);
    return {
        id: prospect.id,
        company: prospect.company,
        contact: prospect.contact,
        note: prospect.note,
        engage_on: isoDaysFromNow(prospect.engageInDays),
        days_until: prospect.engageInDays,
        urgency: urgencyOf(prospect.engageInDays),
        estimated_arr: convert(prospect.estimatedValueUSD, currency),
        owner_id: prospect.owner,
        owner_name: rep?.name ?? prospect.owner,
        owner_initials: initialsOf(rep?.name ?? prospect.owner),
        owner_color: rep?.color ?? "#6b7688",
    };
}

/** Prospects owned by `ownerId`, or every prospect when no owner is given. */
const prospectsScopedTo = (ownerId?: string) =>
    ownerId
        ? getProspectRecords().filter((prospect) => prospect.owner === ownerId)
        : getProspectRecords();

/** Nurtured prospects, soonest engage-by date first. */
export async function getProspects(
    search?: string,
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY,
    ownerId?: string
): Promise<ProspectRow[]> {
    const term = search?.trim().toLowerCase();

    return [...prospectsScopedTo(ownerId)]
        .sort((a, b) => a.engageInDays - b.engageInDays)
        .map((prospect) => toProspectRow(prospect, currency))
        .filter((row) =>
            term
                ? `${row.company} ${row.contact} ${row.note}`.toLowerCase().includes(term)
                : true
        );
}

/** Headline counts for the future pipeline. */
export async function getFuturePipelineSummary(
    currency: BillingCurrency = DEFAULT_DESK_CURRENCY,
    ownerId?: string
): Promise<FuturePipelineSummary> {
    const prospects = prospectsScopedTo(ownerId);
    return {
        prospect_count: prospects.length,
        due_count: prospects.filter((prospect) => prospect.engageInDays <= 0).length,
        total_estimated_arr: convert(
            sum(prospects, (prospect) => prospect.estimatedValueUSD),
            currency
        ),
        currency,
    };
}

/** Fields collected when planning a prospect. */
export interface NewProspectInput {
    company: string;
    contact: string;
    note: string;
    /** ISO date (yyyy-mm-dd). */
    engageOn: string;
    /** In USD, the desk's base currency. */
    estimatedArrUSD: number;
    ownerId: string;
}

export async function addProspect(input: NewProspectInput): Promise<ProspectRow> {
    const company = input.company.trim();
    if (!company) throw new Error("Company name is required.");
    if (!input.engageOn) throw new Error("Pick an engage-by date.");
    if (!Number.isFinite(input.estimatedArrUSD) || input.estimatedArrUSD < 0) {
        throw new Error("Estimated ARR must be a positive number.");
    }

    // The store thinks in offsets from today, so the picked date is converted
    // once here rather than every time a row is read.
    const target = new Date(`${input.engageOn}T00:00:00`);
    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);
    const engageInDays = Math.round(
        (target.getTime() - startOfToday.getTime()) / 86_400_000
    );

    const prospect = addProspectRecord({
        id: allocateId(),
        owner: input.ownerId,
        company,
        contact: input.contact.trim(),
        note: input.note.trim(),
        engageInDays,
        estimatedValueUSD: input.estimatedArrUSD,
    });

    return toProspectRow(prospect, DEFAULT_DESK_CURRENCY);
}

/**
 * Thrown by `engageProspect` when a company with the prospect's name is
 * already on the book. Mirrors the backend's 409 response so the swap to the
 * real endpoint is body-only: the caller resolves it by re-invoking with the
 * existing customer's id, or by cancelling the engage.
 */
export class DuplicateCustomerError extends Error {
    readonly companyName: string;
    readonly existingCustomerId: number;

    constructor(companyName: string, existingCustomerId: number) {
        super(`A customer named "${companyName}" already exists.`);
        this.name = "DuplicateCustomerError";
        this.companyName = companyName;
        this.existingCustomerId = existingCustomerId;
    }
}

/**
 * Promote a prospect into the live pipeline: register the company and open a
 * deal at Activation carrying the nurture note, then drop the prospect.
 *
 * The company is created with only what the prospect record knows; the rest
 * is filled in on the Companies screen, where the new record shows up needing
 * both profiles pushed. Returns the new deal id so the caller can open it.
 *
 * When a company with the prospect's name (case-insensitive, trimmed) is
 * already registered, throws `DuplicateCustomerError` instead of creating a
 * second record. Passing that customer's id as `customerId` links the deal
 * to the existing company instead, and the deal bills in that company's
 * primary billing currency.
 */
export async function engageProspect(
    prospectId: number,
    customerId?: number
): Promise<{ dealId: number }> {
    const prospect = findProspect(prospectId);
    if (!prospect) throw new Error(`Prospect ${prospectId} not found`);

    let company: SeedCompany;
    if (customerId !== undefined) {
        const existing = findCompany(customerId);
        if (!existing) throw new Error(`Company ${customerId} not found`);
        company = existing;
    } else {
        const wanted = prospect.company.trim().toLowerCase();
        const duplicate = getCompanies().find(
            (candidate) => candidate.name.trim().toLowerCase() === wanted
        );
        if (duplicate) throw new DuplicateCustomerError(duplicate.name, duplicate.id);

        const prefix = uniqueCodePrefix(prospect.company);
        company = addCompany({
            id: allocateId(),
            owner: prospect.owner,
            name: prospect.company,
            industry: "Other",
            contact: prospect.contact,
            email: "",
            phone: "",
            tenant: null,
            primaryCurrency: "KES",
            registeredDaysAgo: 0,
            profiles: {
                USD: {
                    code: `${prefix}-USD`,
                    terms: "30 days",
                    tax: "Zero-rated (export)",
                    creditLimit: 25_000,
                    synced: false,
                },
                KES: {
                    code: `${prefix}-KES`,
                    terms: "14 days",
                    tax: "VAT 16%",
                    creditLimit: 1_300_000,
                    synced: false,
                },
            },
        });
    }

    const deal = addDeal({
        id: allocateId(),
        companyId: company.id,
        owner: prospect.owner,
        product: "To be scoped",
        seats: 0,
        valueUSD: prospect.estimatedValueUSD,
        currency: company.primaryCurrency,
        stage: 0,
        status: "open",
        history: [
            {
                stage: DEAL_STAGES[0],
                note: `Moved from future pipeline. ${prospect.note}`,
                daysAgo: 0,
            },
        ],
    });

    removeProspect(prospectId);
    return { dealId: deal.id };
}

// Onboarding endpoints

export interface OnboardingTask {
    index: number;
    label: string;
    completed: boolean;
}

export interface OnboardingRow {
    id: number;
    company_id: number;
    company_name: string;
    plan: string;
    tasks: OnboardingTask[];
    completed_count: number;
    total_count: number;
    /** Completed share, 0 to 1. */
    progress: number;
}

function toOnboardingRow(record: SeedOnboarding): OnboardingRow {
    const tasks = ONBOARDING_TASKS.map((label, index) => ({
        index,
        label,
        completed: record.completed[index] ?? false,
    }));
    const completed = tasks.filter((task) => task.completed).length;

    return {
        id: record.id,
        company_id: record.companyId,
        company_name: findCompany(record.companyId)?.name ?? "Unknown company",
        plan: record.plan,
        tasks,
        completed_count: completed,
        total_count: tasks.length,
        progress: tasks.length ? completed / tasks.length : 0,
    };
}

/** Delivery checklists, least complete first, the ones needing work lead. */
export async function getOnboardingList(): Promise<OnboardingRow[]> {
    return getOnboardingRecords()
        .map(toOnboardingRow)
        .sort((a, b) => a.progress - b.progress);
}

/** Tick or untick one delivery step. */
export async function setOnboardingTask(
    onboardingId: number,
    taskIndex: number,
    completed: boolean
): Promise<OnboardingRow> {
    return toOnboardingRow(setOnboardingTaskRecord(onboardingId, taskIndex, completed));
}

/**
 * Called whenever desk data changes, so views that outlive the screen doing
 * the writing (the sidebar's queue counts) can re-read. Returns an
 * unsubscribe. Becomes cache invalidation once the API is fetch-backed.
 */
export function subscribeToDeskChanges(listener: () => void): () => void {
    return subscribeToStore(listener);
}

// Quotes & pricing endpoints

/** A row on the partner price list. All amounts in USD. */
export interface PriceListRow {
    name: string;
    monthly_per_seat: number;
    annual_per_seat: number;
    /** Annual contract value for ten seats, the usual sanity-check column. */
    ten_seat_arr: number;
}

/** The Microsoft price list, per seat per month, with derived columns. */
export function getPriceList(): PriceListRow[] {
    return PRODUCTS.map((product) => ({
        name: product.name,
        monthly_per_seat: product.usdPerSeatMonth,
        annual_per_seat: product.usdPerSeatMonth * 12,
        ten_seat_arr: product.usdPerSeatMonth * 12 * 10,
    }));
}

export interface QuoteRow {
    id: string;
    company_id: number;
    company_name: string;
    profile_code: string;
    currency: BillingCurrency;
    /** Gross total converted to USD, which is how the list compares quotes. */
    total_usd: number;
    status: QuoteStatus;
    /** ISO date (yyyy-mm-dd). */
    issued_on: string;
}

/** Saved quotes, newest first. */
export async function getQuoteList(): Promise<QuoteRow[]> {
    return getQuoteRecords().map((quote) => ({
        id: quote.id,
        company_id: quote.companyId,
        company_name: findCompany(quote.companyId)?.name ?? "Unknown company",
        profile_code: quote.profileCode,
        currency: quote.currency,
        total_usd: quote.totalUSD,
        status: quote.status,
        issued_on: isoDaysAgo(quote.daysAgo),
    }));
}

/** How a quote line is billed. Annual trades a 15% discount for a year's commitment. */
export type BillingPeriod = "monthly" | "annual";

/** One line as the builder holds it, before pricing. */
export interface QuoteLineInput {
    product: string;
    seats: number;
    billing: BillingPeriod;
    /** Extra partner discount, 0 to 100. */
    discountPercent: number;
}

/** One priced line. Amounts are in the quote's display currency. */
export interface PricedQuoteLine {
    /** Undiscounted list price per seat per month. */
    list_per_seat_month: number;
    /** What the customer actually pays per seat per month. */
    per_seat_month: number;
    /** True when any discount applies, so the UI can strike the list price. */
    is_discounted: boolean;
    /** Per month for monthly lines, per year for annual ones. */
    line_total: number;
}

export interface PricedQuote {
    lines: PricedQuoteLine[];
    subtotal: number;
    tax_rate: number;
    tax_amount: number;
    total: number;
    /** The same total in every other quote currency, for the "≈" line. */
    equivalents: Array<{ currency: QuoteCurrency; amount: number }>;
    currency: QuoteCurrency;
    /** Null for reference currencies, which have no profile to post against. */
    profile: BillingProfile | null;
    /** True when the quote can actually be saved. */
    can_save: boolean;
    /** Why saving is blocked, for the button's title. Null when it is not. */
    blocked_reason: string | null;
}

/**
 * Price a set of lines.
 *
 * Deliberately synchronous and pure: the builder recomputes on every
 * keystroke, and a promise per character would be wasteful and would let
 * results land out of order. It is still the single definition of the
 * arithmetic, so it moves server-side unchanged when the API lands.
 *
 * Tax follows the *profile*, not the currency: a KES profile carries VAT at
 * 16%, a USD profile is a zero-rated export. Reference currencies have no
 * profile, so no tax and no save.
 */
export function priceQuote(
    lines: QuoteLineInput[],
    currency: QuoteCurrency,
    company: CompanyRow | null
): PricedQuote {
    const profile =
        company && isBillingCurrency(currency)
            ? (company.profiles.find((entry) => entry.currency === currency) ?? null)
            : null;

    const hasSellableSeats =
        lines.length > 0 &&
        lines.every((line) => Number.isFinite(line.seats) && line.seats >= MIN_SEATS);

    // Totals are accumulated in USD alongside the display shape, so the
    // subtotal never re-derives itself from already-converted figures.
    let subtotalUSD = 0;

    const priced = lines.map((line): PricedQuoteLine => {
        const product = PRODUCTS.find((candidate) => candidate.name === line.product);
        const listUSD = product?.usdPerSeatMonth ?? 0;

        const annualFactor = line.billing === "annual" ? 1 - ANNUAL_DISCOUNT : 1;
        const discountFactor = 1 - clampPercent(line.discountPercent) / 100;
        const perSeatMonthUSD = listUSD * annualFactor * discountFactor;

        // An annual line bills twelve months up front; a monthly line bills one.
        const months = line.billing === "annual" ? 12 : 1;
        const lineTotalUSD = perSeatMonthUSD * Math.max(0, line.seats) * months;
        subtotalUSD += lineTotalUSD;

        return {
            list_per_seat_month: toCurrency(listUSD, currency),
            per_seat_month: toCurrency(perSeatMonthUSD, currency),
            is_discounted: annualFactor * discountFactor < 1,
            line_total: toCurrency(lineTotalUSD, currency),
        };
    });

    const taxRate = profile?.tax === "VAT 16%" ? 0.16 : 0;
    const totalUSD = subtotalUSD * (1 + taxRate);

    return {
        lines: priced,
        subtotal: toCurrency(subtotalUSD, currency),
        tax_rate: taxRate,
        tax_amount: toCurrency(subtotalUSD * taxRate, currency),
        total: toCurrency(totalUSD, currency),
        equivalents: QUOTE_CURRENCIES.filter((code) => code !== currency).map((code) => ({
            currency: code,
            amount: toCurrency(totalUSD, code),
        })),
        currency,
        profile,
        can_save: profile !== null && hasSellableSeats,
        blocked_reason: blockedReason(profile !== null, hasSellableSeats),
    };
}

/**
 * A line with no seats contributes nothing, so a quote made only of such
 * lines totals zero. Saving that produces a document nobody can act on.
 */
const MIN_SEATS = 1;

function blockedReason(hasProfile: boolean, hasSeats: boolean): string | null {
    if (!hasProfile) return "Switch to USD or KES to save this quote";
    if (!hasSeats) return `Every line needs at least ${MIN_SEATS} seat`;
    return null;
}

const clampPercent = (value: number) =>
    Number.isFinite(value) ? Math.min(Math.max(value, 0), 100) : 0;

const toCurrency = (usd: number, currency: QuoteCurrency) => usd * FX_RATES[currency];

const isBillingCurrency = (currency: QuoteCurrency): currency is BillingCurrency =>
    BILLING_CURRENCIES.includes(currency as BillingCurrency);

export interface SaveQuoteInput {
    companyId: number;
    currency: QuoteCurrency;
    lines: QuoteLineInput[];
}

/**
 * Save a quote against the company's profile for the chosen currency.
 * Saves as Draft, pushing it to the quotations module is a separate step,
 * which is what the Synced status on the list records.
 */
export async function saveQuote(input: SaveQuoteInput): Promise<QuoteRow> {
    const company = findCompany(input.companyId);
    if (!company) throw new Error("Pick a company first.");
    if (input.lines.length === 0) throw new Error("Add at least one line.");
    if (!isBillingCurrency(input.currency)) {
        throw new Error(
            `${input.currency} is a reference currency. Switch to USD or KES to save a quote.`
        );
    }

    const priced = priceQuote(input.lines, input.currency, toCompanyRow(company));
    const profile = company.profiles[input.currency];

    // Ids continue the Q-2031 series rather than restarting per session.
    const nextNumber = 2031 + getQuoteRecords().length;

    const quote = addQuote({
        id: `Q-${nextNumber}`,
        companyId: company.id,
        profileCode: profile.code,
        currency: input.currency,
        totalUSD: priced.total / FX_RATES[input.currency],
        status: "Draft",
        daysAgo: 0,
    });

    return {
        id: quote.id,
        company_id: quote.companyId,
        company_name: company.name,
        profile_code: quote.profileCode,
        currency: quote.currency,
        total_usd: quote.totalUSD,
        status: quote.status,
        issued_on: isoDaysAgo(0),
    };
}

/** Push a saved quote to the quotations module. */
export async function syncQuote(quoteId: string): Promise<void> {
    patchQuote(quoteId, { status: "Synced" });
}

// Formatting

/**
 * Format a desk amount the way the design does: currency symbol, thousands
 * separators, no minor units. Deal values are whole-currency annual contract
 * sums, so cents would be noise.
 */
export function formatDeskMoney(
    amount: number,
    currency: QuoteCurrency = DEFAULT_DESK_CURRENCY
): string {
    return `${CURRENCY_SYMBOLS[currency]}${Math.round(amount).toLocaleString("en-US")}`;
}

/**
 * Money to the minor unit, for quotes.
 *
 * Deal values are whole-currency annual sums where cents are noise, but a
 * quote is a priced offer: per-seat rates and VAT both land on fractions,
 * and a rounded line would not reconcile against the total the customer is
 * asked to sign.
 */
export function formatQuoteMoney(amount: number, currency: QuoteCurrency): string {
    return `${CURRENCY_SYMBOLS[currency]}${amount.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}`;
}

/**
 * Abbreviated form used where space is tight: rep cards, the stage strip and
 * the pipeline's value column.
 *
 * Rolls over at each thousand so the number of digits stays roughly constant:
 * "$740", "$51.8k", "KSh 6.7M". Without the millions step, KES amounts (129x
 * their USD equivalent) read as "KSh 6713.3k", which is longer than the plain
 * number it was meant to shorten.
 */
export function formatDeskMoneyCompact(
    amount: number,
    currency: QuoteCurrency = DEFAULT_DESK_CURRENCY
): string {
    const symbol = CURRENCY_SYMBOLS[currency];
    const magnitude = Math.abs(amount);

    if (magnitude >= 1_000_000_000) return `${symbol}${(amount / 1_000_000_000).toFixed(1)}B`;
    if (magnitude >= 1_000_000) return `${symbol}${(amount / 1_000_000).toFixed(1)}M`;
    if (magnitude >= 1_000) return `${symbol}${(amount / 1_000).toFixed(1)}k`;
    return `${symbol}${Math.round(amount)}`;
}
