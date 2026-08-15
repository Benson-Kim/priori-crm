/**
 * Sales Desk dashboard.
 *
 * Where the pipeline stands, how it closed over the last year, why the lost
 * deals were lost, how each rep is tracking against quota, and who has just
 * come onto the books.
 *
 * The rep filter and the KES/USD currency lens (#57) are page-level state
 * shared by every widget — the same selector pattern as the Business Central
 * dashboard — so the widgets still load together in one effect, now behind a
 * stale-response guard.
 */

import { useEffect, useRef, useState } from "react";

import { LoadingState } from "@/components/ui/LoadingState";
import { Select } from "@/components/ui/Select";
import { Table, type Column } from "@/components/ui/Table";
import { useSalesReps } from "@/hooks/useSalesReps";
import { formatDate, plural } from "@/lib/utils";
import {
    BILLING_CURRENCIES,
    DEFAULT_DESK_CURRENCY,
    formatDeskMoney,
    getSalesDeskDashboard,
    type BillingCurrency,
    type BookingsPoint,
    type CloseReasonSlice,
    type RecentCompany,
    type RepQuotaLine,
    type SalesDeskSummary,
    type StagePipelineLine,
} from "@/services/salesDeskApi";
import { BookingsTrendChart } from "./components/BookingsTrendChart";
import { Avatar } from "@/components/ui/Avatar";
import { KpiCard } from "@/components/ui/KpiCard";
import { DeskPanel } from "./components/DeskPanel";
import { ProgressBar } from "@/components/ui/ProgressBar";

const RECENT_COMPANY_LIMIT = 4;

/** The desk's currency lens offers the billable currencies (KES/USD). */
const DESK_CURRENCY_OPTIONS = BILLING_CURRENCIES.map((currency) => ({
    value: currency,
    label: currency,
}));

interface DashboardData {
    summary: SalesDeskSummary;
    stages: StagePipelineLine[];
    bookings: BookingsPoint[];
    closeReasons: CloseReasonSlice[];
    reps: RepQuotaLine[];
    companies: RecentCompany[];
}

// Section: open pipeline broken down by stage

function PipelineByStagePanel({
    stages,
    currency,
}: Readonly<{ stages: StagePipelineLine[]; currency: BillingCurrency }>) {
    return (
        <DeskPanel title="Active pipeline by stage">
            <div className="flex flex-col gap-3">
                {stages.map((line) => (
                    <div key={line.stage}>
                        <div className="flex items-start justify-between gap-4">
                            <span className="text-xs font-medium text-sd-ink">{line.stage}</span>
                            <span className="text-xs text-sd-muted">
                                {formatDeskMoney(line.amount, currency)} &middot;{" "}
                                {plural(line.deal_count, "deal")}
                            </span>
                        </div>
                        <div className="pt-1">
                            <ProgressBar
                                percent={line.share * 100}
                                height={8}
                                aria-label={`${line.stage} share of open pipeline`}
                            />
                        </div>
                    </div>
                ))}
            </div>
        </DeskPanel>
    );
}

// Section: why the lost deals were lost (QA finding 05: the six enumerated
// reasons are recorded on every lost close but never aggregated anywhere).
// The breakdown is all-time, unlike the quarterly KPI tiles beside it, so the
// panel carries an explicit "All time" subtitle to prevent misreading.

function CloseReasonsPanel({ reasons }: Readonly<{ reasons: CloseReasonSlice[] }>) {
    return (
        <DeskPanel title="Lost deals by reason">
            <p className="-mt-3 pb-3 text-xs text-sd-muted">All time</p>
            <div className="flex flex-col gap-3">
                {reasons.map((line) => (
                    <div key={line.reason}>
                        <div className="flex items-start justify-between gap-4">
                            <span className="text-xs font-medium text-sd-ink">{line.reason}</span>
                            <span className="text-xs text-sd-muted">
                                {plural(line.count, "deal")}
                            </span>
                        </div>
                        <div className="pt-1">
                            <ProgressBar
                                percent={line.share * 100}
                                height={8}
                                aria-label={`${line.reason} share of lost deals`}
                            />
                        </div>
                    </div>
                ))}
            </div>
        </DeskPanel>
    );
}

// Section: each rep's weighted pipeline against quota

function RepQuotaPanel({
    reps,
    currency,
}: Readonly<{ reps: RepQuotaLine[]; currency: BillingCurrency }>) {
    return (
        <DeskPanel title="Rep pipeline vs. quota">
            <div className="flex flex-col gap-4">
                {reps.map((rep) => (
                    <div key={rep.id} className="flex items-center gap-4">
                        <Avatar
                            name={rep.name}
                            initials={rep.initials}
                            color={rep.color}
                            size={32}
                        />

                        <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-4">
                                <span className="truncate text-xs font-medium text-sd-ink">
                                    {rep.name}
                                </span>
                                <span className="shrink-0 text-xs text-sd-muted">
                                    {formatDeskMoney(rep.won_value, currency)} /{" "}
                                    {formatDeskMoney(rep.quarter_target, currency)}
                                </span>
                            </div>
                            <div className="pt-1">
                                <ProgressBar
                                    percent={rep.attainment * 100}
                                    height={8}
                                    color={rep.color}
                                    aria-label={`${rep.name} pipeline against quota`}
                                />
                            </div>
                        </div>

                        <span className="w-10 shrink-0 text-right text-xs font-semibold text-sd-muted">
                            {Math.round(rep.attainment * 100)}%
                        </span>
                    </div>
                ))}
            </div>
        </DeskPanel>
    );
}

// Section: newest companies on the books

const CELL_CLASS = "";

const COMPANY_COLUMNS: Column<RecentCompany>[] = [
    {
        key: "name",
        header: "Company",
        className: CELL_CLASS,
        render: (company) => (
            <span className="font-medium text-sd-ink">{company.name}</span>
        ),
    },
    {
        key: "industry",
        header: "Industry",
        className: CELL_CLASS,
        render: (company) => <span className="text-sd-muted">{company.industry}</span>,
    },
    {
        key: "contact",
        header: "Primary contact",
        className: CELL_CLASS,
        render: (company) => <span className="text-sd-muted">{company.contact}</span>,
    },
    {
        key: "currency",
        header: "Billing",
        className: CELL_CLASS,
        render: (company) => (
            <span className="rounded-full bg-sd-info-bg px-2 py-0.5 text-xs font-semibold text-sd-info">
                {company.billing_currency}
            </span>
        ),
    },
    {
        key: "registered",
        header: "Registered",
        className: CELL_CLASS,
        render: (company) => (
            <span className="text-xs text-sd-muted">
                {formatDate(company.registered_on, "table")}
            </span>
        ),
    },
];

function RecentCompaniesPanel({ companies }: Readonly<{ companies: RecentCompany[] }>) {
    return (
        <DeskPanel title="Recently added companies" bleed>
            <Table
                columns={COMPANY_COLUMNS}
                data={companies}
                rowKey={(company) => String(company.id)}
                variant="sales-desk"
                headerHidden
                emptyMessage="No companies registered yet."
            />
        </DeskPanel>
    );
}

// Page root

export default function SalesDeskDashboardPage() {
    const [data, setData] = useState<DashboardData | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Page-level lenses: which rep's book, in which reporting currency.
    const [ownerId, setOwnerId] = useState("");
    const [currency, setCurrency] = useState<BillingCurrency>(DEFAULT_DESK_CURRENCY);
    const { reps: salesReps } = useSalesReps();

    // Stale-response guard: only the latest request may write state.
    const seqRef = useRef(0);
    useEffect(() => {
        const seq = ++seqRef.current;
        setIsLoading(true);

        getSalesDeskDashboard(ownerId || undefined, currency)
            .then((dashboard) => {
                if (seq !== seqRef.current) return;
                setData({
                    summary: dashboard.summary,
                    stages: dashboard.by_stage,
                    bookings: dashboard.bookings,
                    closeReasons: dashboard.close_reasons,
                    reps: dashboard.rep_quota,
                    companies: dashboard.recent_companies.slice(0, RECENT_COMPANY_LIMIT),
                });
                setError(null);
            })
            .catch((err) => {
                if (seq !== seqRef.current) return;
                setError(err instanceof Error ? err.message : "Failed to load the sales desk");
            })
            .finally(() => {
                if (seq === seqRef.current) setIsLoading(false);
            });
    }, [ownerId, currency]);

    const repOptions = [
        { value: "", label: "All reps" },
        ...salesReps.map((rep) => ({ value: rep.id, label: rep.name })),
    ];

    return (
        <div className="flex flex-col gap-6">
            <div className="flex flex-wrap items-center justify-end gap-3">
                <div className="w-48">
                    <Select
                        aria-label="Filter by sales rep"
                        options={repOptions}
                        value={ownerId}
                        onChange={(event) => setOwnerId(event.target.value)}
                    />
                </div>
                <div className="w-28">
                    <Select
                        aria-label="Reporting currency"
                        options={DESK_CURRENCY_OPTIONS}
                        value={currency}
                        onChange={(event) =>
                            setCurrency(event.target.value as BillingCurrency)
                        }
                    />
                </div>
            </div>

            {error && (
                <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    {error}
                </div>
            )}

            {isLoading && !data && (
                <LoadingState message="Loading the sales desk..." className="h-64" />
            )}

            {data && (
                <>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                        <KpiCard
                            label="Pipeline (weighted)"
                            value={formatDeskMoney(
                                data.summary.weighted_pipeline,
                                data.summary.currency
                            )}
                            subline={plural(data.summary.open_deal_count, "open deal")}
                            tone="info"
                        />
                        <KpiCard
                            label="Total ARR pipeline"
                            value={formatDeskMoney(
                                data.summary.total_pipeline,
                                data.summary.currency
                            )}
                            subline="Unweighted"
                        />
                        <KpiCard
                            label="Won this period"
                            value={formatDeskMoney(
                                data.summary.won_amount,
                                data.summary.currency
                            )}
                            subline={`${plural(data.summary.won_deal_count, "deal")} closed`}
                            tone="success"
                        />
                        <KpiCard
                            label="Lost this period"
                            value={formatDeskMoney(
                                data.summary.lost_amount,
                                data.summary.currency
                            )}
                            subline={`${plural(data.summary.lost_deal_count, "deal")} lost`}
                            tone="danger"
                        />
                    </div>

                    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                        <DeskPanel title="Bookings · 12 months">
                            <BookingsTrendChart
                                data={data.bookings}
                                currency={data.summary.currency}
                            />
                        </DeskPanel>
                        <PipelineByStagePanel
                            stages={data.stages}
                            currency={data.summary.currency}
                        />
                    </div>

                    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                        <RepQuotaPanel reps={data.reps} currency={data.summary.currency} />
                        <CloseReasonsPanel reasons={data.closeReasons} />
                    </div>

                    <RecentCompaniesPanel companies={data.companies} />
                </>
            )}
        </div>
    );
}
