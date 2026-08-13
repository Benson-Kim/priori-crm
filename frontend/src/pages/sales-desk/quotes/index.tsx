/**
 * Quotes and pricing.
 *
 * Two tables on one screen: what has been quoted, and what the catalogue says
 * it should cost. Quote totals are shown in USD equivalent regardless of the
 * currency each was written in, so the column compares like with like.
 */

import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { Table, type Column } from "@/components/ui/Table";
import { formatDate } from "@/lib/utils";
import {
    formatDeskMoney,
    formatQuoteMoney,
    getPriceList,
    getQuoteList,
    type PriceListRow,
    type QuoteRow,
} from "@/services/salesDeskApi";
import { CurrencyChip } from "@/components/ui/Chip";
import { StatusChip } from "@/components/ui/Chip";

const CELL_CLASS = "";
const PRICE_COLUMNS: Column<PriceListRow>[] = [
    {
        key: "name",
        header: "Product",
        className: CELL_CLASS,
        render: (row) => <span className="text-sd-ink">{row.name}</span>,
    },
    {
        key: "monthly",
        header: "Monthly / seat",
        className: CELL_CLASS,
        render: (row) => (
            <span className="text-sd-muted">
                {formatQuoteMoney(row.monthly_per_seat, "USD")}
            </span>
        ),
    },
    {
        key: "annual",
        header: "Annual / seat",
        className: CELL_CLASS,
        render: (row) => (
            <span className="text-sd-muted">
                {formatQuoteMoney(row.annual_per_seat, "USD")}
            </span>
        ),
    },
    {
        key: "arr",
        header: "10-seat ARR",
        className: CELL_CLASS,
        render: (row) => (
            <span className="font-bold text-sd-ink">{formatDeskMoney(row.ten_seat_arr)}</span>
        ),
    },
];

export default function SalesDeskQuotesPage() {
    const navigate = useNavigate();

    const [quotes, setQuotes] = useState<QuoteRow[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // The price list is a static reference table, so no fetch is needed.
    const priceList = useMemo(() => getPriceList(), []);

    useEffect(() => {
        let active = true;

        getQuoteList()
            .then((rows) => {
                if (active) {
                    setQuotes(rows);
                    setError(null);
                }
            })
            .catch((err) => {
                if (active) setError(err instanceof Error ? err.message : "Failed to load quotes");
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, []);

    const quoteColumns = useMemo<Column<QuoteRow>[]>(
        () => [
            {
                key: "id",
                header: "Quote ID",
                className: CELL_CLASS,
                render: (quote) => (
                    <span className="font-mono font-bold text-sd-brand">{quote.id}</span>
                ),
            },
            {
                key: "company",
                header: "Company",
                className: CELL_CLASS,
                render: (quote) => (
                    <span className="text-sd-ink">{quote.company_name}</span>
                ),
            },
            {
                key: "profile",
                header: "Profile",
                className: CELL_CLASS,
                render: (quote) => (
                    <span className="font-mono text-xs text-sd-muted">
                        {quote.profile_code}
                    </span>
                ),
            },
            {
                key: "currency",
                header: "Currency",
                className: CELL_CLASS,
                render: (quote) => <CurrencyChip currency={quote.currency} />,
            },
            {
                key: "total",
                header: "Total (USD eq.)",
                className: CELL_CLASS,
                render: (quote) => (
                    <span className="font-bold text-sd-ink">
                        {formatQuoteMoney(quote.total_usd, "USD")}
                    </span>
                ),
            },
            {
                key: "date",
                header: "Date",
                className: CELL_CLASS,
                render: (quote) => (
                    <span className="text-sd-muted">{formatDate(quote.issued_on)}</span>
                ),
            },
            {
                key: "status",
                header: "Status",
                className: CELL_CLASS,
                render: (quote) => <StatusChip status={quote.status} />,
            },
        ],
        []
    );

    return (
        <div className="flex flex-col gap-5">
            {/* No filters on this screen, so the primary action holds the row. */}
            <div className="flex justify-end">
                <Button
                    variant="primary"
                    onClick={() => navigate("/sales-desk/quotes/new")}
                >
                    <Plus size={16} /> New Quote
                </Button>
            </div>

            {error && (
                <div
                    role="alert"
                    className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700"
                >
                    {error}
                </div>
            )}

            <section className="overflow-hidden rounded-2xl border border-sd-border bg-sd-card shadow-[0_1px_3px_rgba(0,0,0,0.06)]">
                <h2 className="border-b border-sd-border px-5 py-4 text-[13px] font-semibold text-sd-ink">
                    Active quotes
                </h2>
                {isLoading ? (
                    <LoadingState message="Loading quotes..." className="h-40" />
                ) : (
                    <Table
                        columns={quoteColumns}
                        data={quotes}
                        rowKey={(quote) => quote.id}
                        onRowClick={() => navigate("/sales-desk/quotes/new")}
                        variant="sales-desk"
                        emptyMessage="No quotes raised yet."
                    />
                )}
            </section>

            <section className="overflow-hidden rounded-2xl border border-sd-border bg-sd-card shadow-[0_1px_3px_rgba(0,0,0,0.06)]">
                <h2 className="border-b border-sd-border px-5 py-4 text-[13px] font-semibold text-sd-ink">
                    Microsoft 365 · list prices (USD/seat/month)
                </h2>
                <Table
                    columns={PRICE_COLUMNS}
                    data={priceList}
                    rowKey={(row) => row.name}
                    variant="sales-desk"
                />
            </section>
        </div>
    );
}
