/**
 * Quote builder.
 *
 * Three rules drive the screen:
 *
 *  - A quote belongs to a deal. The customer, the reference sequence and the
 *    billing profile are all derived from it server-side, so the picker at the
 *    top selects a deal (labelled by its company), not a bare company.
 *  - Every number is the server's. Lines are sent to
 *    `/deals/{id}/quotes/preview`, which shares its totals code with the save
 *    path, so what the screen shows and what saving persists cannot drift.
 *  - Tax follows the billing profile, not the currency. A KES profile carries
 *    VAT at 16%, a USD profile is a zero-rated export.
 *
 * EUR and GBP have no profile to post against. Selecting one does not reprice
 * the quote: the lines stay in the billing currency and only the totals block
 * switches, reading the server's conversions row.
 */

import { Plus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { StatusChip } from "@/components/ui/Chip";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { Input } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SyncPill } from "@/components/ui/SyncPill";
import { useDebounce } from "@/hooks/useDebounce";
import { cn, formatDate } from "@/lib/utils";
import {
    MIN_SEATS,
    QUOTE_CURRENCIES,
    REFERENCE_CURRENCIES,
    formatDeskMoney,
    formatQuoteMoney,
    getPipelineDeals,
    getQuoteList,
    getSalesPricing,
    isBillingCurrency,
    priceQuote,
    pushQuoteToAccounting,
    saveQuote,
    taxTreatmentLabel,
    type BillingCurrency,
    type BillingPeriod,
    type PipelineDeal,
    type PricedQuote,
    type QuoteCurrency,
    type QuoteLineInput,
    type QuoteRow,
} from "@/services/salesDeskApi";

export default function SalesDeskQuoteBuilderPage() {
    const navigate = useNavigate();

    // Set when the builder is opened from the pipeline deal drawer (#47).
    // Absent, the builder opens on the first open deal and the picker moves.
    const [searchParams] = useSearchParams();
    const linkedDealId = searchParams.get("deal");

    const [deals, setDeals] = useState<PipelineDeal[]>([]);
    const [recentQuotes, setRecentQuotes] = useState<QuoteRow[]>([]);
    const [products, setProducts] = useState<string[]>([]);
    const [annualDiscount, setAnnualDiscount] = useState(0);
    const [dealId, setDealId] = useState<string | null>(linkedDealId);

    // What the totals block is denominated in. USD and KES also decide what the
    // server prices in; EUR and GBP only relabel the totals. Null follows the
    // deal's own currency, so switching deals moves the selection with it until
    // the rep overrides one.
    const [currencyChoice, setCurrencyChoice] = useState<QuoteCurrency | null>(null);
    const [draftLines, setDraftLines] = useState<QuoteLineInput[] | null>(null);

    const [priced, setPriced] = useState<PricedQuote | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isPricing, setIsPricing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [pushingQuoteId, setPushingQuoteId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [priceError, setPriceError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);

    /** A fresh line at the first catalogued product and a round seat count. */
    const newLine = useCallback(
        (): QuoteLineInput => ({
            product: products[0] ?? "",
            seats: 10,
            billing: "annual",
            discountPercent: 0,
        }),
        [products]
    );

    /**
     * The lines on screen. Held as null until the rep touches them so the
     * opening line can name a real product: the catalogue arrives after first
     * paint, and seeding a line before it would offer an empty product.
     */
    const lines = useMemo(
        () => draftLines ?? (products.length > 0 ? [newLine()] : []),
        [draftLines, products, newLine]
    );

    const setLines = useCallback(
        (next: (previous: QuoteLineInput[]) => QuoteLineInput[]) =>
            setDraftLines((previous) =>
                next(previous ?? (products.length > 0 ? [newLine()] : []))
            ),
        [products, newLine]
    );

    // Open deals, the catalogue and the recent-quotes rail. Closed deals are
    // excluded because the server refuses to quote against them.
    useEffect(() => {
        let active = true;

        Promise.all([
            getPipelineDeals({ includeClosed: false }),
            getQuoteList(),
            getSalesPricing(),
        ])
            .then(([openDeals, quoteRows, pricing]) => {
                if (!active) return;
                setDeals(openDeals);
                setRecentQuotes(quoteRows);
                setProducts(pricing.catalog.map((row) => row.name));
                setAnnualDiscount(pricing.annual_discount_percent);
                setDealId((current) => current ?? openDeals[0]?.id ?? null);
                setError(null);
            })
            .catch((err) => {
                if (active)
                    setError(err instanceof Error ? err.message : "Failed to load the builder");
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, [revision]);

    const deal = deals.find((entry) => entry.id === dealId) ?? null;

    /** The discount is named on the control, not discovered later in the total. */
    const billingOptions = useMemo(
        () => [
            { value: "monthly", label: "Monthly" },
            { value: "annual", label: `Annual -${Math.round(annualDiscount)}%` },
        ],
        [annualDiscount]
    );

    const display: QuoteCurrency | null = currencyChoice ?? deal?.billing_currency ?? null;

    /** What the server should price in: null until the user overrides it. */
    const billingCurrency: BillingCurrency | null =
        display !== null && isBillingCurrency(display) ? display : null;

    // Pricing is a round trip, so it is debounced: seats and discounts are
    // typed a character at a time and a request per keystroke would both waste
    // the server and let answers land out of order.
    const pricingKey = useMemo(
        () => JSON.stringify({ dealId, billingCurrency, lines }),
        [dealId, billingCurrency, lines]
    );
    const debouncedKey = useDebounce(pricingKey, 300);
    const requestId = useRef(0);

    useEffect(() => {
        if (dealId === null || lines.length === 0) {
            setPriced(null);
            return;
        }

        const id = ++requestId.current;
        setIsPricing(true);

        priceQuote(dealId, lines, billingCurrency)
            .then((next) => {
                // Only the newest request may write; an earlier one landing
                // late would otherwise show a stale total.
                if (id !== requestId.current) return;
                setPriced(next);
                setPriceError(null);
            })
            .catch((err) => {
                if (id !== requestId.current) return;
                setPriced(null);
                setPriceError(
                    err instanceof Error ? err.message : "Could not price those lines."
                );
            })
            .finally(() => {
                if (id === requestId.current) setIsPricing(false);
            });
        // The debounced key is the trigger; the values it encodes are read live.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [debouncedKey]);

    const setLine = (index: number, patch: Partial<QuoteLineInput>) =>
        setLines((previous) =>
            previous.map((line, position) =>
                position === index ? { ...line, ...patch } : line
            )
        );

    const removeLine = (index: number) =>
        setLines((previous) => previous.filter((_, position) => position !== index));

    const hasSellableSeats = lines.every((line) => line.seats >= MIN_SEATS);
    const isReferenceDisplay = display !== null && REFERENCE_CURRENCIES.includes(display);
    const canSave =
        dealId !== null &&
        priced !== null &&
        hasSellableSeats &&
        !isReferenceDisplay &&
        !isPricing &&
        !isSaving;

    const blockedReason = (() => {
        if (dealId === null) return "Pick a deal to quote against";
        if (!hasSellableSeats) return `Every line needs at least ${MIN_SEATS} seat`;
        if (isReferenceDisplay) return "Switch to USD or KES to save this quote";
        if (priced === null) return priceError ?? "Waiting for pricing";
        return undefined;
    })();

    /** The totals block's currency, and the total in it. */
    const totalCurrency: QuoteCurrency = display ?? priced?.currency ?? "USD";
    const shownTotal =
        priced === null
            ? 0
            : (priced.conversions[totalCurrency] ?? priced.total);

    const save = async () => {
        if (dealId === null) {
            setError("Pick a deal to quote against.");
            return;
        }
        setIsSaving(true);
        setError(null);
        setNotice(null);
        try {
            const quote = await saveQuote({ dealId, currency: billingCurrency, lines });
            const against = priced ? ` against ${priced.profile.code}` : "";
            setNotice(`Quote ${quote.quote_number} created${against}`);
            setDraftLines([newLine()]);
            setRevision((value) => value + 1);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save that quote.");
        } finally {
            setIsSaving(false);
        }
    };

    /**
     * Issue a Draft quote from the rail. A profile accounting has never seen
     * cannot take a posting, so the sync state on the builder's own profile
     * hands the user to the company drawer's sync flow (#46) rather than to a
     * dead end.
     */
    const pushToAccounting = async (quote: QuoteRow) => {
        if (priced && quote.company_id === deal?.company_id && !priced.profile.synced) {
            navigate(`/sales-desk/companies/workspace?company=${quote.company_id}`);
            return;
        }

        setPushingQuoteId(quote.id);
        setError(null);
        setNotice(null);
        try {
            const sent = await pushQuoteToAccounting(quote.id);
            setNotice(`Quote ${sent.quote_number} issued`);
            setRecentQuotes(await getQuoteList());
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not push that quote.");
        } finally {
            setPushingQuoteId(null);
        }
    };

    if (isLoading) {
        return <LoadingState message="Loading the quote builder..." className="h-64" />;
    }

    return (
        <div className="flex flex-col gap-5">
            {error && (
                <div
                    role="alert"
                    className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700"
                >
                    {error}
                </div>
            )}
            {notice && (
                <div
                    role="status"
                    className="rounded-2xl border border-sd-success bg-sd-success-bg p-4 text-sm text-sd-success"
                >
                    {notice}
                </div>
            )}

            <div className="flex flex-col gap-5 xl:flex-row">
                {/* Builder */}
                <section className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-sd-border bg-sd-card">
                    <div className="flex flex-wrap items-center gap-3 border-b border-sd-border px-5 py-3">
                        {/* Borderless per the design: reads as a heading, not a field. */}
                        <div className="min-w-55 flex-1">
                            <InlineSelect
                                variant="sales-desk"
                                aria-label="Deal to quote"
                                placeholder="Select a deal…"
                                value={dealId ?? ""}
                                onChange={(next) => {
                                    setDealId(next);
                                    // Follow the new deal's own currency again.
                                    setCurrencyChoice(null);
                                }}
                                options={deals.map((entry) => ({
                                    value: entry.id,
                                    label: `${entry.company_name} · ${entry.product}`,
                                }))}
                                triggerClassName="border-0 bg-transparent px-0 text-[15px] font-semibold hover:bg-transparent"
                            />
                        </div>

                        <div
                            role="radiogroup"
                            aria-label="Quote currency"
                            className="flex overflow-hidden rounded-xl border border-sd-border"
                        >
                            {QUOTE_CURRENCIES.map((code) => {
                                const isActive = code === display;
                                const isReference = REFERENCE_CURRENCIES.includes(code);
                                return (
                                    <button
                                        key={code}
                                        type="button"
                                        role="radio"
                                        aria-checked={isActive}
                                        title={
                                            isReference
                                                ? `${code} converts for reference. Quotes save in USD or KES.`
                                                : undefined
                                        }
                                        onClick={() => setCurrencyChoice(code)}
                                        className={cn(
                                            "px-3 py-1.5 text-xs font-semibold transition-colors",
                                            isActive
                                                ? "bg-sd-brand text-white"
                                                : "bg-sd-card text-sd-muted hover:text-sd-ink",
                                            !isActive && isReference && "italic"
                                        )}
                                    >
                                        {code}
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    {/* Which profile this posts against */}
                    <p className="flex flex-wrap items-center gap-x-1 border-b border-sd-border px-5 py-2.5 text-[11px] text-sd-muted">
                        {priced ? (
                            <>
                                Posts to{" "}
                                <span className="font-mono font-bold text-sd-ink">
                                    {priced.profile.code}
                                </span>{" "}
                                · {priced.profile.terms} ·{" "}
                                {taxTreatmentLabel(priced.profile.tax)} ·
                                <SyncPill
                                    synced={priced.profile.synced}
                                    className="text-[11px]"
                                />
                                {isReferenceDisplay && (
                                    <span className="text-sd-warn">
                                        {" "}
                                        · {display} is a reference currency,
                                        shown for conversion only
                                    </span>
                                )}
                            </>
                        ) : (
                            <span className={priceError ? "text-sd-warn" : undefined}>
                                {priceError ?? "Pricing…"}
                            </span>
                        )}
                    </p>

                    <div className="overflow-x-auto">
                        <table className="w-full min-w-215 text-left">
                            <thead>
                                <tr className="border-b border-sd-border text-[13px] font-semibold text-sd-ink">
                                    <th scope="col" className="px-5 py-3">Product</th>
                                    <th scope="col" className="px-3 py-3">Seats</th>
                                    <th scope="col" className="px-3 py-3">Billing</th>
                                    <th scope="col" className="px-3 py-3">Extra disc.</th>
                                    <th scope="col" className="px-3 py-3 text-right">Per user/mo</th>
                                    <th scope="col" className="px-3 py-3 text-right">Line total</th>
                                    <th scope="col" className="px-3 py-3">
                                        <span className="sr-only">Remove line</span>
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {lines.map((line, index) => {
                                    const pricedLine = priced?.lines[index] ?? null;
                                    const lineCurrency = priced?.currency ?? "USD";
                                    return (
                                        <tr
                                            key={index}
                                            className="border-b border-sd-border last:border-b-0"
                                        >
                                            <td className="px-5 py-3">
                                                <InlineSelect
                                                    variant="sales-desk"
                                                    aria-label={`Product for line ${index + 1}`}
                                                    value={line.product}
                                                    onChange={(product) =>
                                                        setLine(index, { product })
                                                    }
                                                    options={products.map((name) => ({
                                                        value: name,
                                                        label: name,
                                                    }))}
                                                />
                                            </td>
                                            <td className="px-3 py-3">
                                                <Input
                                                    type="number"
                                                    min={1}
                                                    step={1}
                                                    aria-label={`Seats for line ${index + 1}`}
                                                    value={line.seats}
                                                    onChange={(event) =>
                                                        setLine(index, {
                                                            seats: Number(event.target.value),
                                                        })
                                                    }
                                                    wrapperClassName="h-control bg-white w-20"
                                                    className="py-1.5 text-xs"
                                                />
                                            </td>
                                            <td className="px-3 py-3">
                                                <SegmentedControl
                                                    options={billingOptions}
                                                    value={line.billing}
                                                    aria-label={`Billing period for line ${index + 1}`}
                                                    onChange={(billing) =>
                                                        setLine(index, {
                                                            billing: billing as BillingPeriod,
                                                        })
                                                    }
                                                />
                                            </td>
                                            <td className="px-3 py-3">
                                                <Input
                                                    type="number"
                                                    min={0}
                                                    max={99}
                                                    step={1}
                                                    aria-label={`Extra discount percent for line ${index + 1}`}
                                                    value={line.discountPercent}
                                                    onChange={(event) =>
                                                        setLine(index, {
                                                            discountPercent: Number(
                                                                event.target.value
                                                            ),
                                                        })
                                                    }
                                                    suffix={
                                                        <span className="text-xs text-sd-muted">
                                                            %
                                                        </span>
                                                    }
                                                    wrapperClassName="h-control bg-white w-24"
                                                    className="py-1.5 text-xs"
                                                />
                                            </td>
                                            <td className="px-3 py-3 text-right">
                                                {pricedLine ? (
                                                    <>
                                                        <span className="block text-[13px] font-semibold text-sd-ink">
                                                            {formatQuoteMoney(
                                                                pricedLine.per_seat_month,
                                                                lineCurrency
                                                            )}
                                                        </span>
                                                        {pricedLine.is_discounted && (
                                                            <span className="block text-[11px] text-sd-muted line-through">
                                                                {formatQuoteMoney(
                                                                    pricedLine.list_per_seat_month,
                                                                    lineCurrency
                                                                )}
                                                            </span>
                                                        )}
                                                    </>
                                                ) : (
                                                    <span className="text-[13px] text-sd-muted">
                                                        —
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-3 py-3 text-right">
                                                {pricedLine ? (
                                                    <>
                                                        <span className="block text-[13px] font-bold text-sd-ink">
                                                            {formatQuoteMoney(
                                                                pricedLine.line_total,
                                                                lineCurrency
                                                            )}
                                                        </span>
                                                        <span className="block text-[11px] text-sd-muted">
                                                            {pricedLine.period_label}
                                                        </span>
                                                    </>
                                                ) : (
                                                    <span className="text-[13px] text-sd-muted">
                                                        —
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-3 py-3">
                                                <button
                                                    type="button"
                                                    onClick={() => removeLine(index)}
                                                    disabled={lines.length === 1}
                                                    aria-label={`Remove line ${index + 1}`}
                                                    className="rounded p-1 text-sd-muted transition-colors hover:text-sd-danger disabled:opacity-30"
                                                >
                                                    <X className="size-4" aria-hidden="true" />
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    <div className="flex flex-col gap-4 border-t border-sd-border px-5 py-4 md:flex-row md:items-end md:justify-between">
                        <Button
                            variant="link"
                            className="self-start px-0"
                            onClick={() => setLines((previous) => [...previous, newLine()])}
                        >
                            <Plus size={16} /> Add line
                        </Button>

                        <div
                            aria-busy={isPricing}
                            className={cn(
                                "flex flex-col items-end gap-1 transition-opacity",
                                isPricing && "opacity-50"
                            )}
                        >
                            <p className="text-[11px] text-sd-muted">
                                Subtotal{" "}
                                {formatQuoteMoney(
                                    priced?.subtotal ?? 0,
                                    priced?.currency ?? "USD"
                                )}
                                {priced && priced.tax_amount > 0 && (
                                    <>
                                        {" · "}
                                        {priced.tax_label}{" "}
                                        {formatQuoteMoney(priced.tax_amount, priced.currency)}
                                    </>
                                )}
                            </p>
                            <p className="text-[17px] font-bold text-sd-ink">
                                Total {formatQuoteMoney(shownTotal, totalCurrency)}
                            </p>
                            {priced && (
                                <p className="text-[11px] text-sd-muted">
                                    ≈{" "}
                                    {QUOTE_CURRENCIES.filter(
                                        (code) =>
                                            code !== totalCurrency &&
                                            priced.conversions[code] !== undefined
                                    )
                                        .map((code) =>
                                            formatQuoteMoney(priced.conversions[code] ?? 0, code)
                                        )
                                        .join(" · ")}
                                </p>
                            )}
                        </div>

                        <Button
                            variant="primary"
                            loading={isSaving}
                            disabled={!canSave}
                            title={blockedReason}
                            onClick={() => void save()}
                        >
                            Save quote
                        </Button>
                    </div>
                </section>

                {/* Recent quotes rail */}
                <aside className="w-full shrink-0 xl:w-70">
                    <h2 className="text-[10px] font-bold tracking-[1px] text-sd-muted uppercase">
                        Recent quotes
                    </h2>
                    <div className="flex flex-col gap-3 pt-3">
                        {recentQuotes.length === 0 ? (
                            <p className="text-[11px] text-sd-muted">No quotes raised yet.</p>
                        ) : (
                            recentQuotes.map((quote) => (
                                <div
                                    key={quote.id}
                                    className="rounded-2xl border border-sd-border bg-sd-card p-4 text-left transition-colors hover:border-sd-muted"
                                >
                                    <button
                                        type="button"
                                        onClick={() => navigate("/sales-desk/quotes")}
                                        className="block w-full text-left"
                                    >
                                        <span className="flex items-center justify-between gap-2">
                                            <span className="font-mono text-xs font-bold text-sd-brand">
                                                {quote.quote_number}
                                            </span>
                                            <StatusChip status={quote.status} />
                                        </span>
                                        <span className="block pt-1.5 text-[13px] font-semibold text-sd-ink">
                                            {quote.company_name}
                                        </span>
                                        <span className="block pt-0.5 text-[11px] text-sd-muted">
                                            <span className="font-mono">
                                                {quote.profile_code}
                                            </span>{" "}
                                            · {formatDate(quote.issued_on)} ·{" "}
                                            {formatDeskMoney(quote.total_usd)}
                                        </span>
                                    </button>
                                    {quote.status === "Draft" && (
                                        <button
                                            type="button"
                                            disabled={pushingQuoteId === quote.id}
                                            onClick={() => void pushToAccounting(quote)}
                                            className="block pt-1.5 text-[11px] font-semibold text-sd-brand hover:underline disabled:opacity-50"
                                        >
                                            {pushingQuoteId === quote.id
                                                ? "Pushing…"
                                                : "Push to accounting"}
                                        </button>
                                    )}
                                </div>
                            ))
                        )}
                    </div>
                </aside>
            </div>
        </div>
    );
}
