/**
 * Sales Desk fixtures.
 *
 * There is no `sales_desk` backend module yet, so no deals, stages, reps or
 * bookings endpoints exist. These fixtures are the pipeline modelled in
 * `deal-desk-prototype.html` at the repository root, transcribed into typed
 * records so the screens can be built against real-shaped data.
 *
 * They are consumed only by `salesDeskApi.ts`. When the API lands, that module
 * switches to `apiGet` and this file is deleted; nothing else imports it.
 *
 * Money is held in USD throughout; USD is the desk's base currency and the
 * only one the deal values are quoted in. `salesDeskApi` converts at the
 * display edge.
 */

/** Ordered sales stages. A deal's `stage` is an index into this list. */
export const DEAL_STAGES = [
    "Activation",
    "Qualification",
    "Proposal & Quote",
    "Negotiation",
] as const;

export type DealStage = (typeof DEAL_STAGES)[number];

/**
 * Forecast weighting per stage, the usual CSP-partner shape. Multiplying an
 * open deal's value by its stage probability gives the weighted pipeline.
 */
export const STAGE_PROBABILITY = [0.1, 0.25, 0.5, 0.75] as const;

export type DealStatus = "open" | "won" | "lost";

/** Currencies a company can actually be billed in (each has a posting profile). */
export type BillingCurrency = "USD" | "KES";

/** Every company carries a profile in each of these, in this display order. */
export const BILLING_CURRENCIES: readonly BillingCurrency[] = ["USD", "KES"];

/**
 * Currencies a quote can be priced in. EUR and GBP are reference only: there
 * is no posting profile behind them, so a quote can be shown in them but never
 * saved against them.
 */
export type QuoteCurrency = BillingCurrency | "EUR" | "GBP";

export const QUOTE_CURRENCIES: readonly QuoteCurrency[] = ["USD", "KES", "EUR", "GBP"];

/** Conversion-only currencies, with no billing profile behind them. */
export const REFERENCE_CURRENCIES: readonly QuoteCurrency[] = ["EUR", "GBP"];

/** Units of the named currency per 1 USD. */
export const FX_RATES: Record<QuoteCurrency, number> = {
    USD: 1,
    KES: 129.5,
    EUR: 0.92,
    GBP: 0.79,
};

export const CURRENCY_SYMBOLS: Record<QuoteCurrency, string> = {
    USD: "$",
    KES: "KSh ",
    EUR: "€",
    GBP: "£",
};

/** Discount applied when a line is billed annually rather than monthly. */
export const ANNUAL_DISCOUNT = 0.15;

/** A licence on the partner price list, priced per seat per month in USD. */
export interface SeedProduct {
    name: string;
    usdPerSeatMonth: number;
}

export const PRODUCTS: SeedProduct[] = [
    { name: "Microsoft 365 Business Basic", usdPerSeatMonth: 6.0 },
    { name: "Microsoft 365 Business Standard", usdPerSeatMonth: 12.5 },
    { name: "Microsoft 365 Business Premium", usdPerSeatMonth: 22.0 },
    { name: "Microsoft 365 E3", usdPerSeatMonth: 36.0 },
    { name: "Microsoft 365 E5", usdPerSeatMonth: 57.0 },
    { name: "Exchange Online Plan 1", usdPerSeatMonth: 4.0 },
    { name: "Teams Phone Standard", usdPerSeatMonth: 8.0 },
    { name: "Copilot for Microsoft 365", usdPerSeatMonth: 30.0 },
];

/**
 * Post-sale delivery checklist, in the order the steps are actually worked.
 * Fixed rather than per-customer: the value of the list is that every
 * onboarding can be compared against every other one.
 */
export const ONBOARDING_TASKS = [
    "Kick-off meeting",
    "Tenant & domain setup",
    "Licenses assigned",
    "Data migration",
    "Security baseline",
    "User training",
    "Handover to support",
] as const;

/** One company's delivery, tracked against `ONBOARDING_TASKS`. */
export interface SeedOnboarding {
    id: number;
    companyId: number;
    /** What was sold, "Microsoft 365 E5 · 200 seats". */
    plan: string;
    /** One flag per task, positionally aligned with `ONBOARDING_TASKS`. */
    completed: boolean[];
}

export const SEED_ONBOARDING: SeedOnboarding[] = [
    {
        id: 71,
        companyId: 5,
        plan: "Microsoft 365 E5 · 200 seats",
        completed: [true, true, true, false, false, false, false],
    },
    {
        id: 72,
        companyId: 7,
        plan: "Business Standard · 25 seats",
        completed: [true, true, true, true, true, true, false],
    },
];

/** Where a saved quote has got to. */
export type QuoteStatus = "Draft" | "Pending" | "Synced";

export interface SeedQuote {
    id: string;
    companyId: number;
    /** Billing-profile code the quote posts against. */
    profileCode: string;
    currency: BillingCurrency;
    /** Gross total, held in USD like every other amount on the desk. */
    totalUSD: number;
    status: QuoteStatus;
    daysAgo: number;
}

export const SEED_QUOTES: SeedQuote[] = [
    {
        id: "Q-2031",
        companyId: 1,
        profileCode: "BL-USD",
        currency: "USD",
        totalUSD: 10_930,
        status: "Synced",
        daysAgo: 4,
    },
    {
        id: "Q-2032",
        companyId: 2,
        profileCode: "SM-KES",
        currency: "KES",
        totalUSD: 51_840,
        status: "Draft",
        daysAgo: 2,
    },
    {
        id: "Q-2033",
        companyId: 4,
        profileCode: "KBR-USD",
        currency: "USD",
        totalUSD: 5_760,
        status: "Pending",
        daysAgo: 1,
    },
];

export interface SeedRep {
    id: string;
    name: string;
    role: string;
    /** Chart/avatar colour, straight from the prototype's rep palette. */
    color: string;
    quarterTargetUSD: number;
}

/**
 * A company's posting profile in one currency. Every company carries both a
 * USD and a KES profile; `synced` records whether it has been pushed to
 * accounting yet.
 */
export interface SeedBillingProfile {
    code: string;
    terms: string;
    tax: string;
    /** Credit ceiling, held in USD and converted for display. */
    creditLimitUSD: number;
    synced: boolean;
}

export interface SeedCompany {
    id: number;
    /** Owning rep, keyed to `SeedRep.id`. */
    owner: string;
    name: string;
    industry: string;
    contact: string;
    email: string;
    phone: string;
    /** Microsoft tenant domain, or null before one is provisioned. */
    tenant: string | null;
    /** The billing profile the company transacts on by default. */
    primaryCurrency: BillingCurrency;
    /** Days before today the company was registered. */
    registeredDaysAgo: number;
    profiles: Record<BillingCurrency, SeedBillingProfile>;
}

/** Payment terms a billing profile can be set to. */
export const PAYMENT_TERMS = [
    "Due on receipt",
    "14 days",
    "30 days",
    "45 days",
    "60 days",
] as const;

/**
 * Tax treatments. USD profiles are export sales and carry no VAT; KES
 * profiles settle locally at the standard rate.
 */
export const TAX_TREATMENTS = ["VAT 0%", "VAT 16%", "Exempt"] as const;

/** Industries offered when registering a company. */
export const INDUSTRIES = [
    "Financial services",
    "Healthcare",
    "Education",
    "Logistics & transport",
    "Hospitality & tourism",
    "Manufacturing",
    "Agriculture & export",
    "Professional services",
    "NGO / Non-profit",
    "Retail",
    "Other",
] as const;

/**
 * Billing-profile code prefix: the company's initials, matching the codes on
 * the Companies screens: Baraka Logistics gives BL, Nairobi Dental Group NDG.
 * Falls back to the first three letters for single-word names.
 */
export function companyCodePrefix(name: string): string {
    const initials = name
        .split(/\s+/)
        .filter((word) => /^[A-Za-z]/.test(word))
        .map((word) => word[0])
        .join("")
        .toUpperCase();
    return initials.length >= 2
        ? initials.slice(0, 4)
        : name.replace(/[^A-Za-z]/g, "").slice(0, 3).toUpperCase() || "CO";
}

/**
 * One entry in a deal's audit trail. Every stage change and every logged
 * activity appends one, so the trail doubles as the deal's timeline: the
 * first entry dates the deal, the last is its most recent record.
 */
export interface SeedDealRecord {
    /** Stage label at the time, or "Closed, Won" / "Closed, Lost". */
    stage: string;
    note: string;
    daysAgo: number;
}

export interface SeedDeal {
    id: number;
    companyId: number;
    owner: string;
    product: string;
    seats: number;
    valueUSD: number;
    currency: BillingCurrency;
    /** Index into `DEAL_STAGES`; closed deals sit past the last stage. */
    stage: number;
    status: DealStatus;
    /** Why the deal closed. Present only on won/lost deals. */
    closeReason?: string;
    history: SeedDealRecord[];
}

/** Closing reasons offered when a deal is won. */
export const WON_REASONS = [
    "Price & packaging",
    "Relationship / trust",
    "Product fit",
    "Migration & support offer",
    "Faster response than competitor",
] as const;

/** Closing reasons offered when a deal is lost. */
export const LOST_REASONS = [
    "Price too high",
    "Lost to competitor",
    "Bad timing",
    "No budget",
    "Went direct to Microsoft",
    "No decision / went silent",
] as const;

export interface SeedBooking {
    month: string;
    wonUSD: number;
    lostUSD: number;
}

/**
 * A prospect being nurtured toward a future engagement date. Not yet a
 * company and not yet a deal, just a name, a reason to wait, and the date
 * the waiting ends.
 */
export interface SeedProspect {
    id: number;
    /** Owning rep, keyed to `SeedRep.id`. */
    owner: string;
    company: string;
    contact: string;
    note: string;
    /**
     * Days from today until the prospect should be picked up. Negative means
     * the date has passed and the prospect is overdue.
     */
    engageInDays: number;
    estimatedValueUSD: number;
}

export const SEED_PROSPECTS: SeedProspect[] = [
    {
        id: 51,
        owner: "TK",
        company: "Rift Valley Tea Co.",
        contact: "Daniel Kiprop",
        note: "Budget opens new FY. Wants Business Premium for 80 users.",
        engageInDays: -2,
        estimatedValueUSD: 21_120,
    },
    {
        id: 52,
        owner: "JN",
        company: "Coast Freight Ltd.",
        contact: "Mary Achieng",
        note: "Mid-contract with competitor until November. Unhappy with support.",
        engageInDays: 0,
        estimatedValueUSD: 9_600,
    },
    {
        id: 53,
        owner: "TK",
        company: "Uhuru Academy",
        contact: "Samuel Mwangi",
        note: "Education tenant — engage before Term 1 planning.",
        engageInDays: 45,
        estimatedValueUSD: 6_300,
    },
    {
        id: 54,
        owner: "MW",
        company: "Lakeside Hospital",
        contact: "Dr. Rose Adhiambo",
        note: "New CIO joining in ~3 months. Warm intro promised.",
        engageInDays: 85,
        estimatedValueUSD: 30_000,
    },
];

export const SEED_REPS: SeedRep[] = [
    {
        id: "TK",
        name: "Tabitha Kimani",
        role: "Account Executive",
        color: "#2456e6",
        quarterTargetUSD: 90_000,
    },
    {
        id: "MW",
        name: "Michael Wafula",
        role: "Enterprise Lead",
        color: "#7c3aed",
        quarterTargetUSD: 180_000,
    },
    {
        id: "JN",
        name: "Joy Nduta",
        role: "SMB Executive",
        color: "#0d9488",
        quarterTargetUSD: 60_000,
    },
];

/** USD profiles are export sales; KES profiles carry local VAT. */
const usdProfile = (
    code: string,
    terms: string,
    creditLimitUSD: number,
    synced: boolean
): SeedBillingProfile => ({
    code: `${code}-USD`,
    terms,
    tax: "VAT 0%",
    creditLimitUSD,
    synced,
});

const kesProfile = (
    code: string,
    terms: string,
    creditLimitUSD: number,
    synced: boolean
): SeedBillingProfile => ({
    code: `${code}-KES`,
    terms,
    tax: "VAT 16%",
    creditLimitUSD,
    synced,
});

export const SEED_COMPANIES: SeedCompany[] = [
    {
        id: 1,
        owner: "TK",
        name: "Baraka Logistics",
        industry: "Logistics & transport",
        contact: "Alice Wanjiru",
        email: "alice@baraka.co.ke",
        phone: "+254 720 114 220",
        tenant: "barakalogistics.onmicrosoft.com",
        primaryCurrency: "USD",
        registeredDaysAgo: 24,
        profiles: {
            USD: usdProfile("BL", "30 days", 25_000, true),
            KES: kesProfile("BL", "14 days", 10_000, true),
        },
    },
    {
        id: 2,
        owner: "MW",
        name: "Savannah Microfinance",
        industry: "Financial services",
        contact: "Peter Otieno",
        email: "p.otieno@savannahmfi.co.ke",
        phone: "+254 733 908 771",
        tenant: "savannahmfi.onmicrosoft.com",
        primaryCurrency: "KES",
        registeredDaysAgo: 42,
        profiles: {
            USD: usdProfile("SM", "30 days", 40_000, true),
            KES: kesProfile("SM", "30 days", 60_000, true),
        },
    },
    {
        id: 3,
        owner: "TK",
        name: "Nairobi Dental Group",
        industry: "Healthcare",
        contact: "Grace Muthoni",
        email: "grace@nairobidental.co.ke",
        phone: "+254 711 445 019",
        // No tenant provisioned yet, so the table renders a dash.
        tenant: null,
        primaryCurrency: "KES",
        registeredDaysAgo: 2,
        profiles: {
            USD: usdProfile("NDG", "30 days", 25_000, false),
            KES: kesProfile("NDG", "14 days", 10_000, false),
        },
    },
    {
        id: 4,
        owner: "JN",
        name: "Kilifi Beach Resorts",
        industry: "Hospitality & tourism",
        contact: "Hassan Omar",
        email: "hassan@kilifibeach.com",
        phone: "+254 799 220 400",
        tenant: "kilifibeach.onmicrosoft.com",
        primaryCurrency: "USD",
        registeredDaysAgo: 11,
        profiles: {
            USD: usdProfile("KBR", "30 days", 25_000, true),
            KES: kesProfile("KBR", "14 days", 10_000, false),
        },
    },
    {
        id: 5,
        owner: "MW",
        name: "Acacia Insurance",
        industry: "Financial services",
        contact: "James Kariuki",
        email: "jkariuki@acaciainsure.co.ke",
        phone: "+254 705 663 118",
        tenant: "acaciainsure.onmicrosoft.com",
        primaryCurrency: "USD",
        registeredDaysAgo: 95,
        profiles: {
            USD: usdProfile("AI", "45 days", 150_000, true),
            KES: kesProfile("AI", "30 days", 80_000, true),
        },
    },
    {
        id: 6,
        owner: "JN",
        name: "Tumaini SACCO",
        industry: "Financial services",
        contact: "Faith Njeri",
        email: "faith@tumainisacco.co.ke",
        phone: "+254 722 310 887",
        tenant: null,
        primaryCurrency: "KES",
        registeredDaysAgo: 62,
        profiles: {
            USD: usdProfile("TS", "30 days", 25_000, false),
            KES: kesProfile("TS", "14 days", 10_000, true),
        },
    },
    {
        id: 7,
        owner: "MW",
        name: "Highlands Coffee Exporters",
        industry: "Agriculture & export",
        contact: "Lucy Wambui",
        email: "lucy@highlandscoffee.co.ke",
        phone: "+254 727 550 903",
        tenant: "highlandscoffee.onmicrosoft.com",
        primaryCurrency: "USD",
        registeredDaysAgo: 140,
        profiles: {
            USD: usdProfile("HCE", "30 days", 30_000, true),
            KES: kesProfile("HCE", "14 days", 10_000, true),
        },
    },
];

export const SEED_DEALS: SeedDeal[] = [
    {
        id: 1,
        companyId: 1,
        owner: "TK",
        product: "Microsoft 365 Business Premium",
        seats: 45,
        valueUSD: 45 * 22 * 12,
        currency: "USD",
        stage: 2,
        status: "open",
        history: [
            {
                stage: "Activation",
                note: "Inbound from webinar. Tenant discovery call done — 45 users on Google Workspace.",
                daysAgo: 21,
            },
            {
                stage: "Qualification",
                note: "Budget confirmed with CFO. Security is the driver — needs Intune + Defender.",
                daysAgo: 12,
            },
            {
                stage: "Proposal & Quote",
                note: "Quote Q-2031 sent: Business Premium annual, 8% partner discount.",
                daysAgo: 4,
            },
        ],
    },
    {
        id: 2,
        companyId: 2,
        owner: "MW",
        product: "Microsoft 365 E3",
        seats: 120,
        valueUSD: 120 * 36 * 12,
        currency: "KES",
        stage: 3,
        status: "open",
        history: [
            {
                stage: "Activation",
                note: "Referral from their auditor. Compliance-led project.",
                daysAgo: 40,
            },
            {
                stage: "Qualification",
                note: "IT committee approved scope. 120 seats, phased rollout.",
                daysAgo: 30,
            },
            {
                stage: "Proposal & Quote",
                note: "Quote issued on the KES profile. Competitor (local reseller) also bidding.",
                daysAgo: 14,
            },
            {
                stage: "Negotiation",
                note: "Asked for 10% off + free migration. Escalated pricing to management.",
                daysAgo: 3,
            },
        ],
    },
    {
        id: 3,
        companyId: 3,
        owner: "TK",
        product: "Microsoft 365 Business Standard",
        seats: 18,
        valueUSD: 18 * 12.5 * 12,
        currency: "KES",
        stage: 0,
        status: "open",
        history: [
            {
                stage: "Activation",
                note: "Walk-in enquiry. Booked discovery call for next week.",
                daysAgo: 1,
            },
        ],
    },
    {
        id: 4,
        companyId: 4,
        owner: "JN",
        product: "Teams Phone Standard",
        seats: 60,
        valueUSD: 60 * 8 * 12,
        currency: "USD",
        stage: 1,
        status: "open",
        history: [
            {
                stage: "Activation",
                note: "Existing M365 customer, wants to replace PBX with Teams Phone.",
                daysAgo: 52,
            },
            {
                stage: "Qualification",
                note: "Site survey done across 3 properties. Porting 60 numbers.",
                daysAgo: 38,
            },
        ],
    },
    {
        id: 5,
        companyId: 5,
        owner: "MW",
        product: "Microsoft 365 E5",
        seats: 200,
        valueUSD: 200 * 57 * 12,
        currency: "USD",
        stage: 4,
        status: "won",
        closeReason: "Migration & support offer",
        history: [
            { stage: "Activation", note: "RFP response.", daysAgo: 90 },
            { stage: "Qualification", note: "Shortlisted top 2.", daysAgo: 70 },
            {
                stage: "Proposal & Quote",
                note: "E5 annual, security workshop included.",
                daysAgo: 45,
            },
            {
                stage: "Negotiation",
                note: "Final pricing round, added 24/7 support.",
                daysAgo: 20,
            },
            {
                stage: "Closed — Won",
                note: "Won on white-glove migration plan and local support SLA despite higher price.",
                daysAgo: 15,
            },
        ],
    },
    {
        id: 6,
        companyId: 6,
        owner: "JN",
        product: "Microsoft 365 Business Standard",
        seats: 35,
        valueUSD: 35 * 12.5 * 12,
        currency: "KES",
        stage: 4,
        status: "lost",
        closeReason: "Price too high",
        history: [
            { stage: "Activation", note: "Cold outreach, good first meeting.", daysAgo: 60 },
            {
                stage: "Qualification",
                note: "Committee-driven decision, price sensitive.",
                daysAgo: 50,
            },
            {
                stage: "Proposal & Quote",
                note: "Quoted Business Standard annual.",
                daysAgo: 35,
            },
            {
                stage: "Closed — Lost",
                note: "Board chose a cheaper local email host. Revisit at renewal in 12 months.",
                daysAgo: 28,
            },
        ],
    },
];

/** Closed bookings by month, oldest first, feeds the 12-month trend. */
export const SEED_BOOKINGS: SeedBooking[] = [
    { month: "Sep", wonUSD: 42_000, lostUSD: 18_000 },
    { month: "Oct", wonUSD: 61_000, lostUSD: 12_000 },
    { month: "Nov", wonUSD: 38_000, lostUSD: 27_000 },
    { month: "Dec", wonUSD: 74_000, lostUSD: 9_000 },
    { month: "Jan", wonUSD: 55_000, lostUSD: 21_000 },
    { month: "Feb", wonUSD: 88_000, lostUSD: 15_000 },
    { month: "Mar", wonUSD: 67_000, lostUSD: 33_000 },
    { month: "Apr", wonUSD: 92_000, lostUSD: 11_000 },
    { month: "May", wonUSD: 71_000, lostUSD: 24_000 },
    { month: "Jun", wonUSD: 105_000, lostUSD: 19_000 },
    { month: "Jul", wonUSD: 84_000, lostUSD: 28_000 },
    { month: "Aug", wonUSD: 136_800, lostUSD: 5_250 },
];
