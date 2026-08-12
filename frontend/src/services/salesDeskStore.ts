/**
 * Sales Desk in-memory store.
 *
 * The Sales Desk has no backend yet, but the pipeline is a workflow: a deal
 * that cannot be advanced or closed is not a pipeline. So the fixtures in
 * `salesDeskSeed.ts` are cloned once into a mutable module-level store, and
 * `salesDeskApi`'s write functions edit that clone. Every read goes through
 * the same store, so advancing a deal immediately moves it in the stage
 * strip, the rep cards and the dashboard KPIs.
 *
 * Two consequences worth knowing:
 *  - Changes live for the lifetime of the tab. A reload restores the seed.
 *  - Nothing outside `salesDeskApi` may import this module. When the API
 *    lands, this file is deleted and the service's bodies become HTTP calls;
 *    no component has to change.
 */

import {
    SEED_COMPANIES,
    SEED_DEALS,
    SEED_ONBOARDING,
    SEED_PROSPECTS,
    SEED_QUOTES,
    type BillingCurrency,
    type SeedBillingProfile,
    type SeedCompany,
    type SeedDeal,
    type SeedDealRecord,
    type SeedOnboarding,
    type SeedProspect,
    type SeedQuote,
} from "./salesDeskSeed";

interface DeskState {
    companies: SeedCompany[];
    deals: SeedDeal[];
    prospects: SeedProspect[];
    quotes: SeedQuote[];
    onboarding: SeedOnboarding[];
}

// structuredClone keeps the seed arrays pristine, so a future "reset demo
// data" action is just a matter of building the state again.
function buildState(): DeskState {
    return {
        companies: structuredClone(SEED_COMPANIES),
        deals: structuredClone(SEED_DEALS),
        prospects: structuredClone(SEED_PROSPECTS),
        quotes: structuredClone(SEED_QUOTES),
        onboarding: structuredClone(SEED_ONBOARDING),
    };
}

let state: DeskState = buildState();

/** Highest id in use, so newly created records never collide. */
let nextId =
    Math.max(
        ...SEED_DEALS.map((deal) => deal.id),
        ...SEED_COMPANIES.map((company) => company.id),
        ...SEED_PROSPECTS.map((prospect) => prospect.id)
    ) + 1;

export const allocateId = () => nextId++;

export const getDeals = (): SeedDeal[] => state.deals;

export const getCompanies = (): SeedCompany[] => state.companies;

export const getProspects = (): SeedProspect[] => state.prospects;

export const findProspect = (id: number): SeedProspect | undefined =>
    state.prospects.find((prospect) => prospect.id === id);

export function addProspect(prospect: SeedProspect): SeedProspect {
    state.prospects.push(prospect);
    return prospect;
}

export function removeProspect(id: number): void {
    state.prospects = state.prospects.filter((prospect) => prospect.id !== id);
}

export function addDeal(deal: SeedDeal): SeedDeal {
    state.deals.push(deal);
    return deal;
}

export const getOnboarding = (): SeedOnboarding[] => state.onboarding;

/** Flip one task on one onboarding, in place. */
export function setOnboardingTask(
    onboardingId: number,
    taskIndex: number,
    completed: boolean
): SeedOnboarding {
    const record = state.onboarding.find((entry) => entry.id === onboardingId);
    if (!record) throw new Error(`Onboarding ${onboardingId} not found`);
    if (taskIndex < 0 || taskIndex >= record.completed.length) {
        throw new Error(`Task ${taskIndex} is not on the checklist`);
    }
    record.completed[taskIndex] = completed;
    return record;
}

export const getQuotes = (): SeedQuote[] => state.quotes;

/** Newest first, so a saved quote lands at the top of the recent rail. */
export function addQuote(quote: SeedQuote): SeedQuote {
    state.quotes.unshift(quote);
    return quote;
}

export function patchQuote(id: string, patch: Partial<SeedQuote>): SeedQuote {
    const quote = state.quotes.find((candidate) => candidate.id === id);
    if (!quote) throw new Error(`Quote ${id} not found`);
    Object.assign(quote, patch);
    return quote;
}

export const findDeal = (id: number): SeedDeal | undefined =>
    state.deals.find((deal) => deal.id === id);

export const findCompany = (id: number): SeedCompany | undefined =>
    state.companies.find((company) => company.id === id);

/** Apply a patch to a deal in place. Returns the updated deal. */
export function patchDeal(id: number, patch: Partial<SeedDeal>): SeedDeal {
    const deal = findDeal(id);
    if (!deal) throw new Error(`Deal ${id} not found`);
    Object.assign(deal, patch);
    return deal;
}

/** Append a record to a deal's history, dated today. */
export function appendRecord(id: number, record: Omit<SeedDealRecord, "daysAgo">): SeedDeal {
    const deal = findDeal(id);
    if (!deal) throw new Error(`Deal ${id} not found`);
    deal.history.push({ ...record, daysAgo: 0 });
    return deal;
}

/** Add a newly registered company to the book. Returns the stored record. */
export function addCompany(company: SeedCompany): SeedCompany {
    state.companies.push(company);
    return company;
}

/**
 * Apply a patch to one currency profile in place.
 *
 * Editing the commercial terms of a profile invalidates whatever accounting
 * holds, so any change other than the sync flag itself clears `synced`; the
 * profile has to be pushed again before it can be trusted.
 */
export function patchBillingProfile(
    companyId: number,
    currency: BillingCurrency,
    patch: Partial<SeedBillingProfile>
): SeedCompany {
    const company = findCompany(companyId);
    if (!company) throw new Error(`Company ${companyId} not found`);

    const profile = company.profiles[currency];
    const isTermsChange = Object.keys(patch).some((key) => key !== "synced");
    Object.assign(profile, patch, isTermsChange ? { synced: false } : {});
    return company;
}

/** Take a deal out of the pipeline and park it as a nurtured prospect. */
export function moveToNurture(id: number, note: string, engageInDays: number): void {
    const deal = findDeal(id);
    if (!deal) throw new Error(`Deal ${id} not found`);
    const company = findCompany(deal.companyId);

    state.prospects.push({
        id: allocateId(),
        owner: deal.owner,
        company: company?.name ?? "Unknown company",
        contact: company?.contact ?? "",
        note,
        engageInDays,
        estimatedValueUSD: deal.valueUSD,
    });
    state.deals = state.deals.filter((candidate) => candidate.id !== id);
}

/** Discard every change and restore the seed. Used by tests and demo resets. */
export function resetStore(): void {
    state = buildState();
}
