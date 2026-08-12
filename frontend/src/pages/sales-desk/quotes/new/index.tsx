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
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { Select } from "@/components/ui/Select";
import { cn, formatDate } from "@/lib/utils";
import {
    PRODUCTS,
    QUOTE_CURRENCIES,
    REFERENCE_CURRENCIES,
    formatDeskMoney,
    formatQuoteMoney,
    getCompanyList,
    getQuoteList,
    priceQuote,
    saveQuote,
    type BillingPeriod,
    type CompanyRow,
    type QuoteCurrency,
    type QuoteLineInput,
    type QuoteRow,
} from "@/services/salesDeskApi";
import { SyncStatus } from "../../components/SyncStatus";
import { BillingToggle } from "../components/BillingToggle";
import { QuoteStatusChip } from "../components/QuoteStatusChip";

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

export default function SalesDeskQuoteBuilderPage() {
    const navigate = useNavigate();

    const [companies, setCompanies] = useState<CompanyRow[]>([]);
    const [recentQuotes, setRecentQuotes] = useState<QuoteRow[]>([]);
    const [companyId, setCompanyId] = useState<number | null>(null);
    const [currency, setCurrency] = useState<QuoteCurrency>("USD");
    const [lines, setLines] = useState<QuoteLineInput[]>([newLine()]);

    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);

    useEffect(() => {
        let active = true;

        Promise.all([getCompanyList(), getQuoteList()])
            .then(([companyRows, quoteRows]) => {
                if (!active) return;
                setCompanies(companyRows);
                setRecentQuotes(quoteRows);
                // Default to the first company and the currency it transacts in.
                setCompanyId((current) => current ?? companyRows[0]?.id ?? null);
                setCurrency((current) =>
                    revision === 0 ? (companyRows[0]?.primary_currency ?? "USD") : current
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
    }, [revision]);

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
            const quote = await saveQuote({ companyId, currency, lines });
            setNotice(`Quote ${quote.id} saved against ${quote.profile_code}.`);
            setLines([newLine()]);
            setRevision((value) => value + 1);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save that quote.");
        } finally {
            setIsSaving(false);
        }
    };

    if (isLoading) {
        return <LoadingState message="Loading the quote builder..." className="h-64" />;
    }

    return (
        <div className="flex flex-col gap-5">
            <div>
                <h1 className="text-2xl leading-8 font-bold text-desk-ink">Quotes &amp; pricing</h1>
                <p className="pt-1 text-[13px] text-desk-muted">
                    Per-user pricing, monthly or annual billing, discounts, currency conversion.
                </p>
            </div>

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
                    className="rounded-2xl border border-desk-green bg-desk-green-soft p-4 text-sm text-desk-green"
                >
                    {notice}
                </div>
            )}

            <div className="flex flex-col gap-5 xl:flex-row">
                {/* Builder */}
                <section className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-desk-border bg-desk-surface">
                    <div className="flex flex-wrap items-center gap-3 border-b border-desk-border px-5 py-3">
                        {/* Borderless per the design: reads as a heading, not a field. */}
                        <div className="min-w-[220px] flex-1">
                            <Select
                                id="quote-company"
                                aria-label="Company"
                                value={companyId === null ? "" : String(companyId)}
                                onChange={(event) => {
                                    const next = Number(event.target.value);
                                    setCompanyId(next);
                                    const picked = companies.find((c) => c.id === next);
                                    if (picked) setCurrency(picked.primary_currency);
                                }}
                                options={companies.map((entry) => ({
                                    value: String(entry.id),
                                    label: entry.name,
                                }))}
                                wrapperClassName="border-0 bg-transparent p-0 rounded-none focus-within:ring-0"
                                className="text-[13px] font-semibold text-desk-ink"
                            />
                        </div>

                        <div
                            role="radiogroup"
                            aria-label="Quote currency"
                            className="flex overflow-hidden rounded-xl border border-desk-border"
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
                                                ? "bg-priori-purple text-white"
                                                : "bg-desk-surface text-desk-muted hover:text-desk-ink",
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
                    <p className="flex flex-wrap items-center gap-x-1 border-b border-desk-border px-5 py-2.5 text-[11px] text-desk-muted">
                        {priced.profile ? (
                            <>
                                Posts to{" "}
                                <span className="font-mono font-bold text-desk-ink">
                                    {priced.profile.code}
                                </span>{" "}
                                · {priced.profile.terms} · {priced.profile.tax} ·
                                <SyncStatus synced={priced.profile.synced} className="text-[11px]" />
                            </>
                        ) : (
                            <span className="text-desk-amber">
                                {currency} is a reference currency. Switch to USD or KES to save
                                this quote.
                            </span>
                        )}
                    </p>

                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[860px] text-left">
                            <thead>
                                <tr className="border-b border-desk-border text-[13px] font-semibold text-desk-ink">
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
                                            className="border-b border-desk-border last:border-b-0"
                                        >
                                            <td className="px-5 py-3">
                                                <Select
                                                    aria-label={`Product for line ${index + 1}`}
                                                    value={line.product}
                                                    onChange={(event) =>
                                                        setLine(index, {
                                                            product: event.target.value,
                                                        })
                                                    }
                                                    options={PRODUCT_OPTIONS}
                                                    wrapperClassName="py-1.5 bg-white rounded-xl"
                                                    className="text-[13px]"
                                                />
                                            </td>
                                            <td className="px-3 py-3">
                                                <Input
                                                    type="number"
                                                    min={0}
                                                    step={1}
                                                    aria-label={`Seats for line ${index + 1}`}
                                                    value={line.seats}
                                                    onChange={(event) =>
                                                        setLine(index, {
                                                            seats: Number(event.target.value),
                                                        })
                                                    }
                                                    wrapperClassName="bg-white w-20"
                                                    className="py-1.5 text-xs"
                                                />
                                            </td>
                                            <td className="px-3 py-3">
                                                <BillingToggle
                                                    value={line.billing}
                                                    lineLabel={`line ${index + 1}`}
                                                    onChange={(billing: BillingPeriod) =>
                                                        setLine(index, { billing })
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
                                                        <span className="text-xs text-desk-muted">
                                                            %
                                                        </span>
                                                    }
                                                    wrapperClassName="bg-white w-24"
                                                    className="py-1.5 text-xs"
                                                />
                                            </td>
                                            <td className="px-3 py-3 text-right">
                                                <span className="block text-[13px] font-semibold text-desk-ink">
                                                    {formatQuoteMoney(
                                                        pricedLine.per_seat_month,
                                                        currency
                                                    )}
                                                </span>
                                                {pricedLine.is_discounted && (
                                                    <span className="block text-[11px] text-desk-muted line-through">
                                                        {formatQuoteMoney(
                                                            pricedLine.list_per_seat_month,
                                                            currency
                                                        )}
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-3 py-3 text-right">
                                                <span className="block text-[13px] font-bold text-desk-ink">
                                                    {formatQuoteMoney(
                                                        pricedLine.line_total,
                                                        currency
                                                    )}
                                                </span>
                                                <span className="block text-[11px] text-desk-muted">
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
                                                    className="rounded p-1 text-desk-muted transition-colors hover:text-desk-red disabled:opacity-30"
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

                    <div className="flex flex-col gap-4 border-t border-desk-border px-5 py-4 md:flex-row md:items-end md:justify-between">
                        <Button
                            variant="link"
                            size="sm"
                            className="self-start px-0"
                            onClick={() => setLines((previous) => [...previous, newLine()])}
                        >
                            <Plus size={16} /> Add line
                        </Button>

                        <div className="flex flex-col items-end gap-1">
                            <p className="text-[11px] text-desk-muted">
                                Subtotal {formatQuoteMoney(priced.subtotal, currency)}
                                {priced.tax_rate > 0 && (
                                    <>
                                        {" · "}VAT {Math.round(priced.tax_rate * 100)}%{" "}
                                        {formatQuoteMoney(priced.tax_amount, currency)}
                                    </>
                                )}
                            </p>
                            <p className="text-[17px] font-bold text-desk-ink">
                                Total {formatQuoteMoney(priced.total, currency)}
                            </p>
                            <p className="text-[11px] text-desk-muted">
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
                            title={
                                priced.can_save
                                    ? undefined
                                    : "Switch to USD or KES to save this quote"
                            }
                            onClick={() => void save()}
                        >
                            Save quote
                        </Button>
                    </div>
                </section>

                {/* Recent quotes rail */}
                <aside className="w-full shrink-0 xl:w-[280px]">
                    <h2 className="text-[10px] font-bold tracking-[1px] text-desk-muted uppercase">
                        Recent quotes
                    </h2>
                    <div className="flex flex-col gap-3 pt-3">
                        {recentQuotes.length === 0 ? (
                            <p className="text-[11px] text-desk-muted">No quotes raised yet.</p>
                        ) : (
                            recentQuotes.map((quote) => (
                                <button
                                    key={quote.id}
                                    type="button"
                                    onClick={() => navigate("/sales-desk/quotes")}
                                    className="rounded-2xl border border-desk-border bg-desk-surface p-4 text-left transition-colors hover:border-desk-muted"
                                >
                                    <span className="flex items-center justify-between gap-2">
                                        <span className="font-mono text-xs font-bold text-priori-purple">
                                            {quote.id}
                                        </span>
                                        <QuoteStatusChip status={quote.status} />
                                    </span>
                                    <span className="block pt-1.5 text-[13px] font-semibold text-desk-ink">
                                        {quote.company_name}
                                    </span>
                                    <span className="block pt-0.5 text-[11px] text-desk-muted">
                                        <span className="font-mono">{quote.profile_code}</span> ·{" "}
                                        {formatDate(quote.issued_on)} ·{" "}
                                        {formatDeskMoney(quote.total_usd)}
                                    </span>
                                </button>
                            ))
                        )}
                    </div>
                </aside>
            </div>
        </div>
    );
}
