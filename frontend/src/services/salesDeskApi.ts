/**
 * Sales Desk API service.
 *
 * Backs the Sales Desk screens. Every function here is on the wire: it calls a
 * server route through `apiGet`/`apiPost`/`apiPatch` and is typed from the
 * generated contract. The fixtures the screens were built against are gone;
 * what remains of them is `salesDeskVocabulary.ts`, the display lists (stage
 * names, industries, closing reasons, currency symbols) transcribed from the
 * server's enums rather than invented here.
 *
 * The desk's own routes live under `/sales-desk`; the rest of it is a view
 * over modules that already existed, which is why there is no parallel deal,
 * customer or quote entity: the pipeline is `/deals`, companies are
 * `/customers`, the future pipeline is `/nurture`, and quotes are the quotes
 * module reached through `/deals/{id}/quotes`.
 *
 * Server ids are UUID strings, so every id in the view types below is a
 * `string`.
 *
 * Amounts are the server's, in the currency it priced them in. Nothing is
 * converted, discounted or totalled on this side: `money()` only parses the
 * decimal strings the API sends.
 *
 * The view types below are the screens' contract and stayed stable through the
 * migration, so wiring a function changed its body and not its callers.
 */

import { avatarColor } from "@/components/ui/avatar-utils";
import { apiDownload, apiGet, apiPatch, apiPost } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import type { PaginatedApiResponse } from "@/lib/types";
import {
    BILLING_CURRENCIES,
    CURRENCY_SYMBOLS,
    DEAL_STAGES,
    type BillingCurrency,
    type DealStage,
    type DealStatus,
    type QuoteCurrency,
    type QuoteStatus,
} from "./salesDeskVocabulary";

export {
    BILLING_CURRENCIES,
    QUOTE_CURRENCIES,
    REFERENCE_CURRENCIES
} from "./salesDeskVocabulary";
export type { QuoteCurrency, QuoteStatus } from "./salesDeskVocabulary";

export {
    DEAL_STAGES,
    INDUSTRIES,
    LOST_REASONS,
    PAYMENT_TERMS,
    TAX_TREATMENTS,
    WON_REASONS
} from "./salesDeskVocabulary";
export type { BillingCurrency, DealStage, DealStatus } from "./salesDeskVocabulary";

export const DEFAULT_DESK_CURRENCY: BillingCurrency = "USD";

/** A deal is stale once this many days pass with no logged activity. */
export const STALE_AFTER_DAYS = 30;

/**
 * Desk lists are working sets a rep scans in one go rather than pages through,
 * so they are read whole. 100 is the paginated endpoints' own per-page ceiling;
 * asking for more is a 422, not a bigger page.
 */
const LIST_PAGE_SIZE = 100;

/**
 * The desk's companies endpoint answers in one shot rather than in pages, and
 * caps a request at 500 rows. The directory is read whole, so it asks for the
 * ceiling.
 */
const COMPANY_LIMIT = 500;

/**
 * Read every page of a list endpoint.
 *
 * The desk shows whole lists, and the server caps a page at LIST_PAGE_SIZE, so
 * anything past the first page has to be followed rather than requested in one
 * go. Otherwise a book of more than a hundred deals would quietly show its
 * first hundred.
 */
async function apiGetAll<T>(
    path: string,
    params: Record<string, string | number | boolean | undefined | null> = {}
): Promise<T[]> {
    const rows: T[] = [];
    for (let page = 1; ; page++) {
        const response = await apiGet<PaginatedApiResponse<T>>(path, {
            ...params,
            page,
            per_page: LIST_PAGE_SIZE,
        });
        rows.push(...response.items);
        if (!response.metadata.has_next) return rows;
    }
}

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

/** One rep's closed-won total measured against their quarter quota. */
export interface RepQuotaLine {
    id: string;
    name: string;
    initials: string;
    color: string;
    /** Closed-won in the period. Attainment is measured on won, not forecast. */
    won_value: number;
    quarter_target: number;
    /** Won as a share of quota, 0 to 1. May exceed 1 when a rep beats quota. */
    attainment: number;
}

/** A recently registered company, newest first. */
export interface RecentCompany {
    id: string;
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
    id: string;
    company_id: string;
    company_name: string;
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

/**
 * The server numbers stages from one ("Stage 1 of 5" as a rep says it); the
 * stage path draws segments, so it counts from zero. Converting here keeps the
 * off-by-one in one place rather than in every component that draws a bar.
 */
const toStageIndex = (serverIndex: number) => Math.max(0, serverIndex - 1);

function toDealRecord(record: Schema<"DealActivityResponse">): DealRecord {
    return {
        stage: record.stage_label,
        note: record.note,
        logged_on: record.activity_date,
    };
}

/** Server deal to pipeline row. Every derived figure is server-computed. */
function toPipelineDeal(record: Schema<"DealResponse">): PipelineDeal {
    const history = (record.stage_history ?? []).map(toDealRecord);
    const latest = record.latest_record
        ? toDealRecord(record.latest_record)
        : (history.at(-1) ?? { stage: record.stage_label, note: "", logged_on: "" });

    return {
        id: record.id,
        company_id: record.customer_id,
        company_name: record.company_name,
        product: record.product,
        seats: record.seats,
        value: money(record.value),
        weighted_value: record.weighted_value == null ? null : money(record.weighted_value),
        billing_currency: record.currency as BillingCurrency,
        stage_index: toStageIndex(record.stage_index),
        // `stage_label` is the stage the deal reached, which stays meaningful
        // after it closes, and the stage path still draws it. The chip beside
        // the row asks a different question, so a closed deal answers with its
        // outcome instead.
        stage_label:
            record.status === "open"
                ? record.stage_label
                : record.status === "won"
                  ? "Won"
                  : "Lost",
        status: record.status as DealStatus,
        close_reason: record.close_reason,
        owner_id: record.owner.id,
        owner_name: record.owner.name,
        owner_initials: record.owner.initials,
        owner_color: avatarColor(record.owner.id),
        age_days: record.age_days,
        idle_days: record.idle_days,
        latest_record: latest,
        history,
    };
}



const ACTIVITY_LABELS: Record<ActivityFilterKey, string> = {
    all: "All deals",
    active_this_week: "Active this week",
    quiet_8_30: "8–30 days quiet",
    no_activity_30: "No activity 30d+",
    open_45: "Open 45d+",
};


/**
 * Everything the dashboard renders, in one payload.
 *
 * The server computes these together under a repeatable-read snapshot, so the
 * KPI row, the stage split and the bookings trend are guaranteed to agree with
 * one another. Splitting them across requests would reintroduce the tearing
 * that snapshot exists to prevent.
 */
export interface SalesDeskDashboard {
    summary: SalesDeskSummary;
    by_stage: StagePipelineLine[];
    bookings: BookingsPoint[];
    rep_quota: RepQuotaLine[];
    recent_companies: RecentCompany[];
}

/** Money crosses the wire as a decimal string; the views want numbers. */
const money = (value: string | number | null | undefined): number => Number(value ?? 0);

/**
 * The Sales Desk dashboard, optionally scoped to one owner.
 *
 * Amounts arrive already expressed in the org's desk currency, which the
 * payload names. There is no client-side FX conversion here.
 */
export async function getSalesDeskDashboard(ownerId?: string): Promise<SalesDeskDashboard> {
    const data = await apiGet<Schema<"SalesDeskDashboardResponse">>(
        "/sales-desk/dashboard",
        { owner: ownerId }
    );
    const kpis = data.kpis;

    return {
        summary: {
            weighted_pipeline: money(kpis.pipeline_weighted.value),
            total_pipeline: money(kpis.total_arr_pipeline.value),
            open_deal_count: kpis.pipeline_weighted.open_deal_count,
            won_amount: money(kpis.won_this_period.value),
            won_deal_count: kpis.won_this_period.deal_count,
            lost_amount: money(kpis.lost_this_period.value),
            lost_deal_count: kpis.lost_this_period.deal_count,
            currency: data.currency as BillingCurrency,
        },
        by_stage: data.pipeline_by_stage.map((line) => ({
            // `stage` is the wire enum ("proposal_quote"); the desk labels
            // stages the way the server spells them for display.
            stage: line.stage_label as DealStage,
            amount: money(line.value),
            deal_count: line.deal_count,
            share: line.share,
        })),
        bookings: data.bookings_12_months.map((point) => ({
            month: point.label,
            won: money(point.won_value),
            lost: money(point.lost_value),
        })),
        rep_quota: data.rep_quota.map((line) => ({
            id: line.user.id,
            name: line.user.name,
            initials: line.user.initials,
            color: avatarColor(line.user.id),
            won_value: money(line.won_value),
            quarter_target: money(line.quota),
            attainment: line.percent,
        })),
        recent_companies: data.recent_companies.map((company) => ({
            id: String(company.id),
            name: company.name,
            industry: company.industry ?? "",
            contact: company.contact,
            billing_currency: company.currency as BillingCurrency,
            registered_on: company.created_date,
        })),
    };
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

/** Query string shared by the deal list and its CSV export. */
function dealListParams(query: PipelineQuery) {
    const { ownerId, activity = "all", includeClosed = true, search, status } = query;
    return {
        ownerId,
        // "all" is the absence of a bucket, not a bucket the server knows.
        hygiene: activity === "all" ? undefined : activity,
        showClosed: includeClosed,
        tab: status ?? "all",
        search: search?.trim() || undefined,
    };
}

/**
 * Pipeline rows, ordered the way a rep reads them: open deals first by stage,
 * then won, then lost. The server returns them in that order.
 */
export async function getPipelineDeals(query: PipelineQuery = {}): Promise<PipelineDeal[]> {
    const rows = await apiGetAll<Schema<"DealResponse">>("/deals", dealListParams(query));
    return rows.map(toPipelineDeal);
}

/**
 * The pipeline as a CSV file, honoring the same filters as the deal list.
 *
 * The server builds the file (#47 mandates the server export from #45), so the
 * filters are passed through and the response blob is returned as-is.
 */
export async function exportPipelineCsv(query: PipelineQuery = {}): Promise<Blob> {
    return apiDownload("/sales-desk/exports/pipeline", dealListParams(query));
}

/** Rep cards, stage strip, filter counts and the open-pipeline total. */
export async function getPipelineOverview(ownerId?: string): Promise<PipelineOverview> {
    const data = await apiGet<Schema<"PipelineOverviewResponse">>(
        "/sales-desk/pipeline/overview",
        { ownerId }
    );

    /** Hygiene counts arrive keyed by the server's names, not the filter keys. */
    const counts: Record<ActivityFilterKey, number> = {
        all: data.hygiene.all,
        active_this_week: data.hygiene.active_week,
        quiet_8_30: data.hygiene.quiet_8_30,
        no_activity_30: data.hygiene.no_activity_30,
        open_45: data.hygiene.open_45,
    };

    return {
        reps: data.reps.map((rep) => ({
            id: rep.user.id,
            name: rep.user.name,
            initials: rep.user.initials,
            color: avatarColor(rep.user.id),
            open_deal_count: rep.open_count,
            open_value: money(rep.open_value),
            win_rate: rep.win_rate,
            cold_deal_count: rep.cold_count,
        })),
        team: {
            name: "Whole team",
            open_deal_count: data.team.open_count,
            open_value: money(data.team.open_value),
            win_rate: data.team.win_rate,
            cold_deal_count: data.team.cold_count,
        },
        stages: data.stages.map((column) => ({
            stage: column.stage_label as DealStage,
            deal_count: column.count,
            value: money(column.value),
            average_age_days: column.avg_days_in_stage,
        })),
        closed: {
            deal_count: data.closed.count,
            won_count: data.closed.won_count,
            lost_count: data.closed.lost_count,
            win_rate: data.closed.win_rate,
        },
        filters: (Object.keys(ACTIVITY_LABELS) as ActivityFilterKey[]).map((key) => ({
            key,
            label: ACTIVITY_LABELS[key],
            count: counts[key],
        })),
        open_pipeline_value: money(data.open_pipeline_total),
        currency: data.currency as BillingCurrency,
    };
}

/**
 * The reps a deal or company can be assigned to.
 *
 * There is no user directory endpoint, but the pipeline overview already
 * returns every rep with a book, which is the same set the owner filters and
 * the "assign to" selects need. Reusing it keeps one server-side definition of
 * who a rep is instead of inventing a second.
 */
export async function getSalesReps(): Promise<RepPipelineCard[]> {
    const overview = await getPipelineOverview();
    return overview.reps;
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

export async function getDealDetail(dealId: string): Promise<DealDetail> {
    const record = await apiGet<Schema<"DealResponse">>(`/deals/${dealId}`);
    const profile = record.billing_profile;

    return {
        deal: toPipelineDeal(record),
        billing_profile: {
            code: profile?.code ?? "",
            terms: profile?.payment_terms ?? "",
            tax: profile?.tax_treatment ?? "",
            synced: profile?.synced ?? false,
        },
        billed_value: money(record.value),
        next_stage:
            record.status === "open" && record.stage_index < record.stage_total - 1
                ? DEAL_STAGES[record.stage_index + 1]
                : null,
    };
}

// Pipeline writes
//
// Each resolves with the server's updated deal, so callers refresh from the
// returned value rather than refetching the list.

/** Record what happened without moving the deal. */
export async function logDealActivity(dealId: string, note: string): Promise<PipelineDeal> {
    return toPipelineDeal(
        await apiPost<Schema<"DealResponse">>(`/deals/${dealId}/activities`, { note })
    );
}

/** Move the deal to the next stage, recording why. */
export async function advanceDeal(dealId: string, note: string): Promise<PipelineDeal> {
    return toPipelineDeal(
        await apiPost<Schema<"DealResponse">>(`/deals/${dealId}/advance`, { note })
    );
}

/** Close the deal as won or lost, with a reason and a closing note. */
export async function closeDeal(
    dealId: string,
    outcome: Extract<DealStatus, "won" | "lost">,
    reason: string,
    note: string
): Promise<PipelineDeal> {
    return toPipelineDeal(
        await apiPost<Schema<"DealResponse">>(`/deals/${dealId}/close`, {
            result: outcome,
            reason,
            note,
        })
    );
}

/**
 * Park the deal out of the active pipeline until a date, so it resurfaces in
 * the future pipeline when due.
 */
export async function moveDealToFuturePipeline(
    dealId: string,
    note: string,
    engageInDays = 90
): Promise<void> {
    const until = new Date();
    until.setDate(until.getDate() + engageInDays);
    await apiPost<Schema<"DealResponse">>(`/deals/${dealId}/park`, {
        note,
        parkedUntil: until.toISOString().slice(0, 10),
    });
    // The deal reappears as a prospect, which the future-pipeline badge counts.
    announceDeskChange();
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
    id: string;
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
    id: string;
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

export interface CompanyQuery {
    /** Free-text match over name, industry, contact and tenant. */
    search?: string;
    /** Restrict to companies with an unsynced profile. */
    unsynced?: boolean;
}

/** Server company row to the directory row the table renders. */
function toCompanyRowFromServer(record: Schema<"DeskCompanyRow">): CompanyRow {
    return {
        id: record.id,
        name: record.name,
        industry: record.industry ?? "",
        contact: record.contact,
        email: record.email,
        phone: record.phone,
        tenant: record.tenant,
        primary_currency: record.primary_currency as BillingCurrency,
        registered_on: record.registered_on,
        owner_id: record.owner?.id ?? "",
        owner_name: record.owner?.name ?? "Unassigned",
        owner_initials: record.owner?.initials ?? "—",
        owner_color: avatarColor(record.owner?.id ?? ""),
        profiles: record.profiles.map((profile) => ({
            currency: profile.currency as BillingCurrency,
            code: profile.code,
            terms: profile.payment_terms,
            tax: profile.tax_treatment,
            credit_limit: money(profile.credit_limit),
            synced: profile.synced,
            is_default: profile.is_default,
        })),
        needs_sync: record.needs_sync,
        open_deal_count: record.open_deal_count,
        closed_deal_count: record.closed_deal_count,
        total_deal_count: record.total_deal_count,
    };
}

/** The companies directory, newest registration first. */
export async function getCompanyList(query: CompanyQuery = {}): Promise<CompanyRow[]> {
    const data = await apiGet<Schema<"DeskCompaniesResponse">>("/sales-desk/companies", {
        search: query.search?.trim() || undefined,
        unsynced: query.unsynced || undefined,
        limit: COMPANY_LIMIT,
    });
    return data.items.map(toCompanyRowFromServer);
}

/**
 * One company plus its deals, for the drawer.
 *
 * The row comes back through the same desk endpoint the directory uses, so
 * the drawer and the table can never disagree about a company. The deals are
 * read from the pipeline: `/deals` has no customer filter, but its search
 * matches the company name, so the name narrows the page and the id filters
 * out any other company the name also matched.
 */
export async function getCompanyDetail(companyId: string): Promise<CompanyDetail> {
    const directory = await apiGet<Schema<"DeskCompaniesResponse">>(
        "/sales-desk/companies",
        { limit: COMPANY_LIMIT }
    );
    const record = directory.items.find((item) => item.id === companyId);
    if (!record) throw new Error(`Company ${companyId} not found`);

    const company = toCompanyRowFromServer(record);
    const dealRows = await apiGetAll<Schema<"DealResponse">>("/deals", {
        search: company.name,
        showClosed: true,
        tab: "all",
    });

    return {
        company,
        deals: dealRows
            .filter((deal) => deal.customer_id === companyId)
            .map((deal) => ({
                id: deal.id,
                product: deal.product,
                billing_currency: deal.currency as BillingCurrency,
                // In the deal's own billing currency, which is what the card
                // labels it with. Converting to the caller's currency here
                // would print a USD figure beside a KES chip.
                value: money(deal.value),
                stage_index: toStageIndex(deal.stage_index),
                stage_label: deal.stage_label,
                status: deal.status as DealStatus,
                close_reason: deal.close_reason ?? null,
                age_days: deal.age_days,
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
    companyId: string,
    currency: BillingCurrency,
    patch: BillingProfilePatch
): Promise<CompanyRow> {
    if (patch.credit_limit !== undefined) {
        if (!Number.isFinite(patch.credit_limit) || patch.credit_limit < 0) {
            throw new Error("Credit limit must be a positive number.");
        }
    }

    await apiPatch<Schema<"BillingProfileResponse">>(
        `/customers/${companyId}/profiles/${currency}`,
        {
            ...(patch.terms !== undefined ? { paymentTerms: patch.terms } : {}),
            ...(patch.tax !== undefined ? { taxTreatment: patch.tax } : {}),
            ...(patch.credit_limit !== undefined
                ? { creditLimit: String(patch.credit_limit) }
                : {}),
        }
    );

    // The response is the one profile; the drawer redraws the whole company
    // (both profiles, the sync banner, the deal counts), so the row is re-read
    // rather than patched in place.
    return reloadCompany(companyId);
}

/** Push one currency profile to accounting. */
export async function syncBillingProfile(
    companyId: string,
    currency: BillingCurrency
): Promise<CompanyRow> {
    await apiPost<Schema<"BillingProfileResponse">>(
        `/customers/${companyId}/profiles/${currency}/sync`,
        {}
    );
    return reloadCompany(companyId);
}

/** Push both currency profiles to accounting. */
export async function syncBothProfiles(companyId: string): Promise<CompanyRow> {
    await apiPost(`/customers/${companyId}/profiles/sync-all`, {});
    return reloadCompany(companyId);
}

/**
 * Re-read one company through the desk directory, after a write.
 *
 * Every company write changes the needs-sync queue behind the nav badge, so
 * the re-read is also where the change is announced.
 */
async function reloadCompany(companyId: string): Promise<CompanyRow> {
    const directory = await apiGet<Schema<"DeskCompaniesResponse">>(
        "/sales-desk/companies",
        { limit: COMPANY_LIMIT }
    );
    const record = directory.items.find((item) => item.id === companyId);
    if (!record) throw new Error(`Company ${companyId} not found`);
    announceDeskChange();
    return toCompanyRowFromServer(record);
}

/** Fields collected when registering a company. */
export interface NewCompanyInput {
    name: string;
    industry: string;
    /**
     * The primary contact, as a first and last name. The customers module
     * holds the two separately and requires both, so the desk asks for both
     * rather than guessing at a split.
     */
    firstName: string;
    lastName: string;
    email: string;
    phone: string;
    tenant?: string;
    primaryCurrency: BillingCurrency;
    ownerId: string;
}

/**
 * Register a company.
 *
 * The customers module owns the whole registration: it allocates the profile
 * codes (unique even when two company names reduce to the same initials) and
 * opens one billing profile per currency, all unsynced: nothing exists in
 * accounting until someone pushes it, which is what the Companies screen's
 * "Needs sync" filter surfaces.
 */
export async function createCompany(input: NewCompanyInput): Promise<CompanyRow> {
    const name = input.name.trim();
    if (!name) throw new Error("Company name is required.");
    if (!input.firstName.trim() || !input.lastName.trim()) {
        throw new Error("A primary contact's first and last name are required.");
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.email.trim())) {
        throw new Error("Enter a valid email address.");
    }

    const created = await apiPost<Schema<"CustomerResponse">>("/customers", {
        customerType: "business",
        companyName: name,
        firstName: input.firstName.trim(),
        lastName: input.lastName.trim(),
        email: input.email.trim(),
        phone: input.phone.trim(),
        currency: input.primaryCurrency,
        primaryCurrency: input.primaryCurrency,
        ...(input.industry.trim() ? { industry: input.industry.trim() } : {}),
        ...(input.tenant?.trim() ? { tenantDomain: input.tenant.trim() } : {}),
        ...(input.ownerId ? { ownerId: input.ownerId } : {}),
    });

    return reloadCompany(created.id);
}

// Future pipeline endpoints

/** How urgent a prospect's engage-by date is. */
export type ProspectUrgency = "overdue" | "today" | "scheduled";

export interface ProspectRow {
    id: string;
    company: string;
    contact: string;
    note: string;
    /** ISO date (yyyy-mm-dd) the prospect should be picked up. */
    engage_on: string;
    /** Days until that date. Negative once it has passed. */
    days_until: number;
    urgency: ProspectUrgency;
    /** Chip text exactly as the server computed it: "Overdue", "Today", "45d". */
    due_label: string;
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

/** Server due-state to the urgency the chips and dots key on. */
const urgencyOf = (state: string): ProspectUrgency =>
    state === "overdue" ? "overdue" : state === "today" ? "today" : "scheduled";

function toProspectRow(record: Schema<"NurtureProspectResponse">): ProspectRow {
    const owner = record.owner;
    return {
        id: record.id,
        company: record.company,
        contact: record.contact ?? "",
        note: record.note ?? "",
        engage_on: record.engage_on,
        // The server counts against the org-local accounting boundary; a
        // client-side date subtraction would disagree either side of midnight.
        days_until: record.due_state.in_days ?? 0,
        urgency: urgencyOf(record.due_state.state),
        due_label: record.due_state.label,
        estimated_arr: money(record.est_arr),
        owner_id: owner?.id ?? "",
        owner_name: owner?.name ?? "Unassigned",
        owner_initials: owner?.initials ?? "—",
        owner_color: avatarColor(owner?.id ?? ""),
    };
}

/** Nurtured prospects, soonest engage-by date first. */
export async function getProspects(
    search?: string,
    ownerId?: string
): Promise<ProspectRow[]> {
    const rows = await apiGetAll<Schema<"NurtureProspectResponse">>("/nurture", {
        search: search?.trim() || undefined,
        ownerId,
    });
    return rows.map(toProspectRow);
}

/** Headline counts for the future pipeline. */
export async function getFuturePipelineSummary(
    ownerId?: string,
    search?: string
): Promise<FuturePipelineSummary> {
    const summary = await apiGet<Schema<"NurtureSummary">>("/nurture/summary", {
        ownerId,
        search: search?.trim() || undefined,
    });
    return {
        prospect_count: summary.count,
        due_count: summary.due_now,
        total_estimated_arr: money(summary.total_est_arr),
        currency: DEFAULT_DESK_CURRENCY,
    };
}

/** Fields collected when planning a prospect. */
export interface NewProspectInput {
    company: string;
    contact: string;
    note: string;
    /** ISO date (yyyy-mm-dd). Omitted lets the server default to +90 days. */
    engageOn: string;
    estimatedArr: number;
    ownerId: string;
}

export async function addProspect(input: NewProspectInput): Promise<ProspectRow> {
    const created = await apiPost<Schema<"NurtureProspectResponse">>("/nurture", {
        company: input.company.trim(),
        contact: input.contact.trim() || null,
        note: input.note.trim() || null,
        engageOn: input.engageOn || null,
        estArr: input.estimatedArr,
        ownerId: input.ownerId || null,
    });
    // A prospect planned for today lands straight in the due queue.
    announceDeskChange();
    return toProspectRow(created);
}

/**
 * The customer details engaging a prospect must supply.
 *
 * A prospect records only a company, a contact name and a note, but a
 * customer needs an email and phone, so the engage dialog collects them.
 */
export interface EngageCustomerInput {
    firstName: string;
    lastName: string;
    email: string;
    phone: string;
}

/**
 * Thrown when a customer with the prospect's email is already on the book.
 * Mirrors the backend's 409: the caller resolves it by re-invoking with the
 * existing customer's id, or by cancelling the engage.
 */
export class DuplicateCustomerError extends Error {
    readonly companyName: string;
    readonly existingCustomerId: string;

    constructor(companyName: string, existingCustomerId: string) {
        super(`A customer named "${companyName}" already exists.`);
        this.name = "DuplicateCustomerError";
        this.companyName = companyName;
        this.existingCustomerId = existingCustomerId;
    }
}

/**
 * Promote a prospect into the live pipeline.
 *
 * The server creates the customer and an open deal at Activation and drops
 * the prospect, all in one transaction. Product, seats and currency are left
 * to the server's defaults (first catalogued product, one seat, the
 * customer's primary currency); the rep adjusts them in the deal drawer.
 */
export async function engageProspect(
    prospectId: string,
    input: { customerId: string } | { customer: EngageCustomerInput; company: string }
): Promise<{ dealId: string }> {
    const body =
        "customerId" in input
            ? { customerId: input.customerId }
            : {
                  customer: {
                      customerType: "business",
                      companyName: input.company,
                      firstName: input.customer.firstName,
                      lastName: input.customer.lastName,
                      email: input.customer.email,
                      phone: input.customer.phone,
                  },
              };

    try {
        const deal = await apiPost<Schema<"DealResponse">>(
            `/nurture/${prospectId}/engage`,
            body
        );
        // The prospect leaves the due queue behind the nav badge.
        announceDeskChange();
        return { dealId: deal.id };
    } catch (error) {
        // A 409 carries the existing customer, which the caller offers to link.
        const conflict = error as { status?: number; data?: Record<string, unknown> };
        if (conflict.status === 409) {
            const existing = String(conflict.data?.customer_id ?? "");
            throw new DuplicateCustomerError(
                "company" in input ? input.company : "",
                existing
            );
        }
        throw error;
    }
}

// Onboarding endpoints

export interface OnboardingTask {
    /**
     * The server's 1-based step number, which is both what the card counts off
     * and the path segment the toggle endpoint takes. Named for what it is so
     * neither use has to guess at the base.
     */
    position: number;
    label: string;
    completed: boolean;
}

export interface OnboardingRow {
    id: string;
    company_id: string;
    company_name: string;
    plan: string;
    tasks: OnboardingTask[];
    completed_count: number;
    total_count: number;
    /** Completed share, 0 to 1. */
    progress: number;
}

/**
 * Server checklist to view row.
 *
 * Steps carry their own `position`, which is what the toggle endpoint keys on,
 * so it is preserved rather than re-derived from array order.
 */
function toOnboardingRow(record: Schema<"OnboardingResponse">): OnboardingRow {
    const tasks: OnboardingTask[] = record.steps.map((step) => ({
        position: step.position,
        label: step.label,
        completed: step.done,
    }));

    return {
        id: record.id,
        company_id: record.customer_id,
        company_name: record.company_name,
        plan: record.plan_label,
        tasks,
        completed_count: record.tasks_completed,
        total_count: record.tasks_total,
        progress: record.tasks_total ? record.tasks_completed / record.tasks_total : 0,
    };
}

/** Delivery checklists, least complete first, the ones needing work lead. */
export async function getOnboardingList(search?: string): Promise<OnboardingRow[]> {
    const rows = await apiGetAll<Schema<"OnboardingResponse">>("/onboardings", {
        search: search?.trim() || undefined,
    });
    return rows
        .map(toOnboardingRow)
        .sort((first, second) => first.progress - second.progress);
}

/** Tick or untick one delivery step. */
export async function setOnboardingTask(
    onboardingId: string,
    taskPosition: number
): Promise<OnboardingRow> {
    return toOnboardingRow(
        await apiPost<Schema<"OnboardingResponse">>(
            `/onboardings/${onboardingId}/tasks/${taskPosition}/toggle`,
            {}
        )
    );
}

/**
 * Called whenever a desk write lands, so views that outlive the screen doing
 * the writing (the sidebar's queue counts) can re-read. Returns an
 * unsubscribe.
 *
 * Deliberately a bare notification rather than a cache: the desk has no client
 * store to invalidate, and each listener re-reads exactly what it needs.
 */
export function subscribeToDeskChanges(listener: () => void): () => void {
    deskListeners.add(listener);
    return () => {
        deskListeners.delete(listener);
    };
}

const deskListeners = new Set<() => void>();

/** Announce a write. Listener failures must not break the write that caused them. */
function announceDeskChange(): void {
    deskListeners.forEach((listener) => {
        try {
            listener();
        } catch {
            // A stale subscriber is not the writer's problem.
        }
    });
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

/** The org's sales pricing: what it sells, and on what annual terms. */
export interface SalesPricing {
    catalog: PriceListRow[];
    /**
     * Percentage points off the per-seat monthly price for annual billing.
     * The builder names it on its billing control and the server applies it,
     * so reading it here keeps the label honest if an org changes it.
     */
    annual_discount_percent: number;
}

/**
 * The org's price list and annual terms.
 *
 * The annual and ten-seat figures are computed server-side so the table and
 * the quote builder can never disagree about what list price means.
 */
export async function getSalesPricing(): Promise<SalesPricing> {
    const settings = await apiGet<Schema<"SalesPricingSettings">>(
        "/owner/settings/sales-pricing"
    );
    return {
        annual_discount_percent: money(settings.annual_billing_discount_pct),
        catalog: settings.catalog.map((entry) => ({
            name: entry.name,
            monthly_per_seat: money(entry.usd_per_seat_month),
            annual_per_seat: money(entry.usd_per_seat_year),
            ten_seat_arr: money(entry.usd_ten_seat_arr),
        })),
    };
}

/** Just the price table, for the screen that only renders it. */
export async function getPriceList(): Promise<PriceListRow[]> {
    return (await getSalesPricing()).catalog;
}

export interface QuoteRow {
    id: string;
    /** User-facing reference ("Q-2031"), which is what every screen labels. */
    quote_number: string;
    company_id: string;
    company_name: string;
    profile_code: string;
    currency: BillingCurrency;
    /** Gross total converted to USD, which is how the list compares quotes. */
    total_usd: number;
    status: QuoteStatus;
    /** ISO date (yyyy-mm-dd). */
    issued_on: string;
    /** Deal the quote was raised from (deal drawer, #47), or null. */
    deal_id: string | null;
}

function toQuoteRow(quote: Schema<"QuoteSummary">): QuoteRow {
    return {
        id: quote.id,
        quote_number: quote.quote_number,
        company_id: quote.customer_id,
        company_name: quote.customer_name,
        profile_code: quote.billing_profile_code ?? "",
        currency: quote.currency as BillingCurrency,
        total_usd: money(quote.total_usd_equivalent ?? quote.total_due),
        status: quote.display_status as QuoteStatus,
        issued_on: quote.transaction_date,
        deal_id: quote.deal_id ?? null,
    };
}

/** Saved quotes, newest first. */
export async function getQuoteList(): Promise<QuoteRow[]> {
    const rows = await apiGetAll<Schema<"QuoteSummary">>("/quotes");
    return rows.map(toQuoteRow);
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

/** One priced line, in the currency the quote is written in. */
export interface PricedQuoteLine {
    /** Undiscounted list price per seat per month. */
    list_per_seat_month: number;
    /** What the customer actually pays per seat per month. */
    per_seat_month: number;
    /** True when any discount applies, so the UI can strike the list price. */
    is_discounted: boolean;
    /** Per month for monthly lines, per year for annual ones. */
    line_total: number;
    /** "per month" or "per year", as the server labels the period. */
    period_label: string;
}

export interface PricedQuote {
    lines: PricedQuoteLine[];
    subtotal: number;
    /** The profile's tax treatment, e.g. "VAT 16%" or "Zero-rated (export)". */
    tax_label: string;
    tax_amount: number;
    total: number;
    /** The billing currency the server actually priced in. */
    currency: BillingCurrency;
    /** The profile the quote posts against. Always present when priced. */
    profile: BillingProfile;
    /**
     * The total in every reference currency the server converts to. Drives
     * both the "≈" line and what a reference-currency selection displays.
     */
    conversions: Partial<Record<QuoteCurrency, number>>;
}

/** A quote-builder line as the server's prefill contract expects it. */
const toLineInput = (line: QuoteLineInput) => ({
    product: line.product,
    seats: Math.max(0, Math.trunc(line.seats)),
    billingPeriod: line.billing,
    extraDiscountPct: String(clampPercent(line.discountPercent)),
});

/**
 * Price a set of lines against a deal.
 *
 * Every figure here is the server's: catalogue list price, the annual
 * billing discount, the extra discount, the VAT line and the reference
 * conversions all come back from `/deals/{id}/quotes/preview`, which shares
 * `calculate_totals` with the create path, so a preview can never disagree
 * with what saving would persist.
 *
 * Tax follows the *profile*, not the currency: a KES profile carries VAT at
 * 16%, a USD profile is a zero-rated export. `currency` may be left null to
 * take the deal's own billing currency. EUR and GBP are reference currencies
 * with no profile behind them, so they are never sent: the builder reads them
 * off `conversions` instead.
 */
export async function priceQuote(
    dealId: string,
    lines: QuoteLineInput[],
    currency: BillingCurrency | null
): Promise<PricedQuote> {
    const preview = await apiPost<Schema<"DealQuotePreviewResponse">>(
        `/deals/${dealId}/quotes/preview`,
        {
            lines: lines.map(toLineInput),
            ...(currency === null ? {} : { currency }),
        }
    );

    const total = money(preview.total_due);
    return {
        lines: preview.lines.map((line) => {
            const list = money(line.list_price_per_user_month);
            const discounted = money(line.discounted_price_per_user_month);
            return {
                list_per_seat_month: list,
                per_seat_month: discounted,
                is_discounted: discounted < list,
                line_total: money(line.line_total),
                period_label: line.period_label,
            };
        }),
        subtotal: money(preview.subtotal),
        tax_label: preview.tax_label,
        tax_amount: money(preview.tax_total),
        total,
        currency: preview.currency as BillingCurrency,
        profile: {
            code: preview.billing_profile.code,
            terms: preview.billing_profile.payment_terms,
            tax: preview.billing_profile.tax_treatment,
            synced: preview.billing_profile.synced,
        },
        conversions: {
            USD: money(preview.conversions.usd),
            EUR: money(preview.conversions.eur),
            GBP: money(preview.conversions.gbp),
            [preview.currency as BillingCurrency]: total,
        },
    };
}

/**
 * A line with no seats contributes nothing, so a quote made only of such
 * lines totals zero. Saving that produces a document nobody can act on.
 */
export const MIN_SEATS = 1;

const clampPercent = (value: number) =>
    Number.isFinite(value) ? Math.min(Math.max(value, 0), 99) : 0;

export const isBillingCurrency = (currency: QuoteCurrency): currency is BillingCurrency =>
    BILLING_CURRENCIES.includes(currency as BillingCurrency);

export interface SaveQuoteInput {
    /**
     * The deal the quote is raised from. Quotes belong to deals server-side:
     * the customer, the reference sequence and the billing profile are all
     * derived from it, so there is no company-only save.
     */
    dealId: string;
    /** Null takes the deal's own billing currency. */
    currency: BillingCurrency | null;
    lines: QuoteLineInput[];
}

/**
 * A newly created quote, as much of it as the create response carries.
 *
 * Narrower than `QuoteRow` on purpose: the quotes module's create response
 * has no company name, profile code or USD equivalent on it, and inventing
 * them client-side is exactly the drift the preview/create split avoids. The
 * builder already holds the profile from its preview, and the list refreshes
 * from `getQuoteList` straight after.
 */
export interface SavedQuote {
    id: string;
    quote_number: string;
    currency: BillingCurrency;
    /** In the quote's own currency. */
    total_due: number;
    status: QuoteStatus;
}

/**
 * Save a quote against the deal's profile for the chosen currency.
 *
 * Saves as Draft; pushing it through to accounting is a separate step, which
 * is what the Synced status on the list records.
 */
export async function saveQuote(input: SaveQuoteInput): Promise<SavedQuote> {
    if (input.lines.length === 0) throw new Error("Add at least one line.");
    if (input.lines.some((line) => !(line.seats >= MIN_SEATS))) {
        throw new Error(`Every line needs at least ${MIN_SEATS} seat.`);
    }

    const quote = await apiPost<Schema<"QuoteResponse">>(
        `/deals/${input.dealId}/quotes`,
        {
            lines: input.lines.map(toLineInput),
            ...(input.currency === null ? {} : { currency: input.currency }),
        }
    );

    return {
        id: quote.id,
        quote_number: quote.quote_number,
        currency: quote.currency as BillingCurrency,
        total_due: money(quote.total_due),
        status: quote.display_status as QuoteStatus,
    };
}

/** Push a saved quote to the quotations module. */
export async function syncQuote(quoteId: string): Promise<void> {
    // "Posts to {profile}" on the desk is the quotes module's conversion to an
    // invoice: QuoteDisplayStatus maps Synced <- invoiced, i.e. pushed through
    // to accounting. There is no separate sync route, and adding one would
    // give the same state two owners.
    await apiPost<Schema<"QuoteConvertToInvoiceResponse">>(
        `/quotes/${quoteId}/convert-to-invoice`,
        {}
    );
}

/**
 * Thrown when a quote cannot be issued because the billing profile it posts
 * against has never been pushed to accounting. Carries the company id so the
 * UI can deep-link straight into the company drawer's sync flow (#46).
 *
 * The guard is raised on the desk rather than by the quotes module: the
 * module's mark-sent transition only cares about the quote's own status, and
 * the sync state lives on the customer's billing profile, which the desk
 * already has from the companies list.
 */
export class UnsyncedProfileError extends Error {
    readonly companyId: string;
    readonly profileCode: string;

    constructor(companyId: string, profileCode: string) {
        super(
            `Profile ${profileCode} has not been synced to accounting yet. ` +
            "Push the profile first, then issue the quote."
        );
        this.name = "UnsyncedProfileError";
        this.companyId = companyId;
        this.profileCode = profileCode;
    }
}

/**
 * Issue a Draft quote. The mark-sent transition is what moves a quote from
 * Draft to Pending on the desk.
 */
export async function pushQuoteToAccounting(quoteId: string): Promise<SavedQuote> {
    const sent = await apiPost<Schema<"QuoteResponse">>(`/quotes/${quoteId}/mark-sent`, {});
    return {
        id: sent.id,
        quote_number: sent.quote_number,
        currency: sent.currency as BillingCurrency,
        total_due: money(sent.total_due),
        status: sent.display_status as QuoteStatus,
    };
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
