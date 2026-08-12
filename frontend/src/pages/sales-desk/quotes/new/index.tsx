/**
 * Quote builder.
 *
 * Three rules drive the screen:
 *
 *  - Price is held in USD and converted for display, so switching currency
 *    re-labels the quote without re-pricing it.
 *  - Tax follows the billing profile, not the currency. A KES profile carries
 *    VAT at 16%, a USD profile is a zero-rated export.
 *  - EUR and GBP convert for reference but have no profile to post against,
 *    so a quote cannot be saved in them.
 *
 * Pricing recomputes synchronously on every edit via `priceQuote`, a pure
 * function in the service, so the arithmetic has one definition.
 */

import { Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { Input } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { cn, formatDate } from "@/lib/utils";
import {
    ANNUAL_DISCOUNT,
    PRODUCTS,
    QUOTE_CURRENCIES,
    REFERENCE_CURRENCIES,
    UnsyncedProfileError,
    formatDeskMoney,
    formatQuoteMoney,
    getCompanyList,
    getDealDetail,
    getQuoteList,
    priceQuote,
    pushQuoteToAccounting,
    saveQuote,
    type BillingPeriod,
    type CompanyRow,
    type QuoteCurrency,
    type QuoteLineInput,
    type QuoteRow,
} from "@/services/salesDeskApi";
import { SyncPill } from "@/components/ui/SyncPill";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { StatusChip } from "@/components/ui/Chip";

/** A fresh line at the mid-tier licence and a round seat count. */
const newLine = (): QuoteLineInput => ({
    product: PRODUCTS[1].name,
    seats: 10,
    billing: "annual",
    discountPercent: 0,
});

const PRODUCT_OPTIONS = PRODUCTS.map((product) => ({
    value: product.name,
    label: product.name,
}));

/** The discount is named on the control, not discovered later in the total. */
const BILLING_OPTIONS = [
    { value: "monthly", label: "Monthly" },
    { value: "annual", label: `Annual -${Math.round(ANNUAL_DISCOUNT * 100)}%` },
];

export default function SalesDeskQuoteBuilderPage() {
    const navigate = useNavigate();

    // Set when the builder is opened from the pipeline deal drawer (#47);
    // the quote then preselects that deal's company and billing currency
    // and saves carrying the deal's id. Optional: absent, the builder is
    // the plain standalone screen.
    const [searchParams] = useSearchParams();
    const dealId = Number(searchParams.get("deal")) || null;

    const [companies, setCompanies] = useState<CompanyRow[]>([]);
    const [recentQuotes, setRecentQuotes] = useState<QuoteRow[]>([]);
    const [companyId, setCompanyId] = useState<number | null>(null);
    const [currency, setCurrency] = useState<QuoteCurrency>("USD");
    const [lines, setLines] = useState<QuoteLineInput[]>([newLine()]);

    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [pushingQuoteId, setPushingQuoteId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);

    useEffect(() => {
        let active = true;

        Promise.all([
            getCompanyList(),
            getQuoteList(),
            // A stale or mistyped ?deal= link degrades to the default
            // preselection rather than blocking the whole builder.
            dealId === null ? Promise.resolve(null) : getDealDetail(dealId).catch(() => null),
        ])
            .then(([companyRows, quoteRows, dealDetail]) => {
                if (!active) return;
                setCompanies(companyRows);
                setRecentQuotes(quoteRows);
                // Preselect the deal's company and billing currency when opened
                // from the deal drawer; otherwise default to the first company
                // and the currency it transacts in.
                const deal = dealDetail?.deal ?? null;
                setCompanyId(
                    (current) => current ?? deal?.company_id ?? companyRows[0]?.id ?? null
                );
                setCurrency((current) =>
                    revision === 0
                        ? (deal?.billing_currency ??
                              companyRows[0]?.primary_currency ??
                              "USD")
                        : current
                );
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
    }, [revision, dealId]);

    const company = companies.find((entry) => entry.id === companyId) ?? null;
    const priced = useMemo(
        () => priceQuote(lines, currency, company),
        [lines, currency, company]
    );

    const setLine = (index: number, patch: Partial<QuoteLineInput>) =>
        setLines((previous) =>
            previous.map((line, position) =>
                position === index ? { ...line, ...patch } : line
            )
        );

    const removeLine = (index: number) =>
        setLines((previous) => previous.filter((_, position) => position !== index));

    const save = async () => {
        if (companyId === null) {
            setError("Pick a company first.");
            return;
        }
        setIsSaving(true);
        setError(null);
        setNotice(null);
        try {
            const quote = await saveQuote({
                companyId,
                currency,
                lines,
                ...(dealId === null ? {} : { dealId }),
            });
            setNotice(`Quote ${quote.id} created against ${quote.profile_code}`);
            setLines([newLine()]);
            setRevision((value) => value + 1);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save that quote.");
        } finally {
            setIsSaving(false);
        }
    };

    /**
     * Issue a Draft quote to accounting from the rail. A profile accounting
     * has never seen cannot take a posting, so the typed error hands the
     * user to the company drawer's sync flow (#46) instead of a dead end.
     */
    const pushToAccounting = async (quoteId: string) => {
        setPushingQuoteId(quoteId);
        setError(null);
        setNotice(null);
        try {
            const quote = await pushQuoteToAccounting(quoteId);
            setNotice(`Quote ${quote.id} pushed to accounting against ${quote.profile_code}`);
            setRecentQuotes(await getQuoteList());
        } catch (err) {
            if (err instanceof UnsyncedProfileError) {
                navigate(`/sales-desk/companies/workspace?company=${err.companyId}`);
                return;
            }
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
                        <div className="min-w-[220px] flex-1">
                            <InlineSelect
                                variant="sales-desk"
                                aria-label="Company"
                                placeholder="Select a company…"
                                value={companyId === null ? "" : String(companyId)}
                                onChange={(next) => {
                                    const id = Number(next);
                                    setCompanyId(id);
                                    const picked = companies.find((c) => c.id === id);
                                    if (picked) setCurrency(picked.primary_currency);
                                }}
                                options={companies.map((entry) => ({
                                    value: String(entry.id),
                                    label: entry.name,
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
                                const isActive = code === currency;
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
                                        onClick={() => setCurrency(code)}
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
                        {priced.profile ? (
                            <>
                                Posts to{" "}
                                <span className="font-mono font-bold text-sd-ink">
                                    {priced.profile.code}
                                </span>{" "}
                                · {priced.profile.terms} · {priced.profile.tax} ·
                                <SyncPill synced={priced.profile.synced} className="text-[11px]" />
                            </>
                        ) : (
                            <span className="text-sd-warn">
                                {currency}: reference currency for conversion only — no
                                accounting profile exists
                            </span>
                        )}
                    </p>

                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[860px] text-left">
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
                                    const pricedLine = priced.lines[index];
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
                                                    options={PRODUCT_OPTIONS}
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
                                                    options={BILLING_OPTIONS}
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
                                                    max={100}
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
                                                <span className="block text-[13px] font-semibold text-sd-ink">
                                                    {formatQuoteMoney(
                                                        pricedLine.per_seat_month,
                                                        currency
                                                    )}
                                                </span>
                                                {pricedLine.is_discounted && (
                                                    <span className="block text-[11px] text-sd-muted line-through">
                                                        {formatQuoteMoney(
                                                            pricedLine.list_per_seat_month,
                                                            currency
                                                        )}
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-3 py-3 text-right">
                                                <span className="block text-[13px] font-bold text-sd-ink">
                                                    {formatQuoteMoney(
                                                        pricedLine.line_total,
                                                        currency
                                                    )}
                                                </span>
                                                <span className="block text-[11px] text-sd-muted">
                                                    {line.billing === "annual"
                                                        ? "per year"
                                                        : "per month"}
                                                </span>
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

                        <div className="flex flex-col items-end gap-1">
                            <p className="text-[11px] text-sd-muted">
                                Subtotal {formatQuoteMoney(priced.subtotal, currency)}
                                {priced.tax_rate > 0 && (
                                    <>
                                        {" · "}VAT {Math.round(priced.tax_rate * 100)}%{" "}
                                        {formatQuoteMoney(priced.tax_amount, currency)}
                                    </>
                                )}
                            </p>
                            <p className="text-[17px] font-bold text-sd-ink">
                                Total {formatQuoteMoney(priced.total, currency)}
                            </p>
                            <p className="text-[11px] text-sd-muted">
                                ≈{" "}
                                {priced.equivalents
                                    .map((entry) =>
                                        formatQuoteMoney(entry.amount, entry.currency)
                                    )
                                    .join(" · ")}
                            </p>
                        </div>

                        <Button
                            variant="primary"
                            loading={isSaving}
                            disabled={!priced.can_save}
                            title={priced.blocked_reason ?? undefined}
                            onClick={() => void save()}
                        >
                            Save quote
                        </Button>
                    </div>
                </section>

                {/* Recent quotes rail */}
                <aside className="w-full shrink-0 xl:w-[280px]">
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
                                                {quote.id}
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
                                            onClick={() => void pushToAccounting(quote.id)}
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
