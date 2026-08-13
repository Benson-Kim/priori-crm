/**
 * Sales Desk display vocabularies.
 *
 * What the desk's controls offer, and what its labels say. Every list here has
 * a counterpart enum on the server, and offering a value outside one would let
 * a user pick something the API rejects. These are transcriptions rather than
 * definitions: change the server's enum and this file follows.
 *
 * Nothing here computes anything. Amounts, discounts, conversions and totals
 * are the server's and arrive already calculated; this file only knows how to
 * spell things.
 *
 * Consumed by `salesDeskApi.ts`, which re-exports what the screens need so
 * they have one import for the desk.
 */

/*
 * The stage vocabulary lives in lib/ so the shared stage components can use
 * it too.
 */
import {
    DEAL_STAGES,
    type DealStage,
    type DealStatus,
} from "@/lib/sales-desk-stages";

export { DEAL_STAGES, type DealStage, type DealStatus };

/** Currencies a company can actually be billed in (each has a posting profile). */
export type BillingCurrency = "USD" | "KES";

/** Every company carries a profile in each of these, in this display order. */
export const BILLING_CURRENCIES: readonly BillingCurrency[] = ["USD", "KES"];

/**
 * Currencies a quote can be shown in. EUR and GBP are reference only: there is
 * no posting profile behind them, so the server converts a quote into them for
 * display but refuses to price one in them.
 */
export type QuoteCurrency = BillingCurrency | "EUR" | "GBP";

export const QUOTE_CURRENCIES: readonly QuoteCurrency[] = ["USD", "KES", "EUR", "GBP"];

/** Conversion-only currencies, with no billing profile behind them. */
export const REFERENCE_CURRENCIES: readonly QuoteCurrency[] = ["EUR", "GBP"];

export const CURRENCY_SYMBOLS: Record<QuoteCurrency, string> = {
    USD: "$",
    KES: "KSh ",
    EUR: "€",
    GBP: "£",
};

/** How the desk labels a quote's lifecycle. Mirrors the API's QuoteDisplayStatus. */
export type QuoteStatus = "Draft" | "Pending" | "Synced";

/** Payment terms offered on a billing profile. */
export const PAYMENT_TERMS = [
    "On receipt",
    "14 days",
    "30 days",
    "45 days",
    "60 days",
] as const;

/**
 * Tax treatments. USD profiles are export sales and carry no VAT; KES
 * profiles settle locally at the standard rate. Wire values from the
 * customers API's TaxTreatment enum.
 */
export const TAX_TREATMENTS = ["Zero-rated (export)", "VAT 16%", "Exempt"] as const;

/**
 * The industry vocabulary, which must stay identical to the server's
 * `Industry` enum (and the IN-list in migration d5e6f7a8b9c0), because the
 * customers API rejects anything outside it.
 */
export const INDUSTRIES = [
    "Financial services",
    "Healthcare",
    "Education",
    "Logistics & transport",
    "Hospitality & tourism",
    "Agriculture & export",
    "Insurance",
    "Manufacturing",
    "Professional services",
    "NGO / Non-profit",
    "Retail",
    "Other",
] as const;

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
