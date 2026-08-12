/**
 * Sales Desk dashboard.
 *
 * Where the pipeline stands, how it closed over the last year, how each rep is
 * tracking against quota, and who has just come onto the books.
 *
 * Unlike the Business Central dashboard this page has no period or currency
 * filters, so all five widgets load together in one effect rather than each
 * owning its own fetch and stale-response guard.
 */

import { useEffect, useState } from "react";

import { LoadingState } from "@/components/ui/LoadingState";
import { Table, type Column } from "@/components/ui/Table";
import { formatDate } from "@/lib/utils";
import {
    DEFAULT_DESK_CURRENCY,
    formatDeskMoney,
    getBookingsTrend,
    getPipelineByStage,
    getRecentCompanies,
    getRepQuotaProgress,
    getSalesDeskSummary,
    type BookingsPoint,
    type RecentCompany,
    type RepQuotaLine,
    type SalesDeskSummary,
    type StagePipelineLine,
} from "@/services/salesDeskApi";
import { BookingsTrendChart } from "./components/BookingsTrendChart";
import { DeskKpiCard } from "./components/DeskKpiCard";
import { DeskPanel } from "./components/DeskPanel";
import { DeskProgressBar } from "./components/DeskProgressBar";

const RECENT_COMPANY_LIMIT = 4;

/** Pluralise a count against its noun: "1 deal", "4 deals". */
const plural = (count: number, noun: string) =>
    `${count} ${noun}${count === 1 ? "" : "s"}`;

interface DashboardData {
    summary: SalesDeskSummary;
    stages: StagePipelineLine[];
    bookings: BookingsPoint[];
    reps: RepQuotaLine[];
    companies: RecentCompany[];
}

// Section: open pipeline broken down by stage

function PipelineByStagePanel({ stages }: Readonly<{ stages: StagePipelineLine[] }>) {
    return (
        <DeskPanel title="Active pipeline by stage">
            <div className="flex flex-col gap-3">
                {stages.map((line) => (
                    <div key={line.stage}>
                        <div className="flex items-start justify-between gap-4">
                            <span className="text-xs font-medium text-desk-ink">{line.stage}</span>
                            <span className="text-xs text-desk-muted">
                                {formatDeskMoney(line.amount)} &middot; {plural(line.deal_count, "deal")}
                            </span>
                        </div>
                        <div className="pt-1">
                            <DeskProgressBar
                                value={line.share}
                                color="var(--color-priori-purple)"
                                label={`${line.stage} share of open pipeline`}
                            />
                        </div>
                    </div>
                ))}
            </div>
        </DeskPanel>
    );
}

// Section: each rep's weighted pipeline against quota

function RepQuotaPanel({ reps }: Readonly<{ reps: RepQuotaLine[] }>) {
    return (
        <DeskPanel title="Rep pipeline vs. quota">
            <div className="flex flex-col gap-4">
                {reps.map((rep) => (
                    <div key={rep.id} className="flex items-center gap-4">
                        <span
                            className="flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
                            style={{ backgroundColor: rep.color }}
                            aria-hidden="true"
                        >
                            {rep.initials}
                        </span>

                        <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-4">
                                <span className="truncate text-xs font-medium text-desk-ink">
                                    {rep.name}
                                </span>
                                <span className="shrink-0 text-xs text-desk-muted">
                                    {formatDeskMoney(rep.weighted_pipeline)} /{" "}
                                    {formatDeskMoney(rep.quarter_target)}
                                </span>
                            </div>
                            <div className="pt-1">
                                <DeskProgressBar
                                    value={rep.attainment}
                                    color={rep.color}
                                    label={`${rep.name} pipeline against quota`}
                                />
                            </div>
                        </div>

                        <span className="w-10 shrink-0 text-right text-xs font-semibold text-desk-muted">
                            {Math.round(rep.attainment * 100)}%
                        </span>
                    </div>
                ))}
            </div>
        </DeskPanel>
    );
}

// Section: newest companies on the books

const CELL_CLASS = "px-5 py-3 text-[13px]";

const COMPANY_COLUMNS: Column<RecentCompany>[] = [
    {
        key: "name",
        header: "Company",
        className: CELL_CLASS,
        render: (company) => (
            <span className="font-medium text-desk-ink">{company.name}</span>
        ),
    },
    {
        key: "industry",
        header: "Industry",
        className: CELL_CLASS,
        render: (company) => <span className="text-desk-muted">{company.industry}</span>,
    },
    {
        key: "contact",
        header: "Primary contact",
        className: CELL_CLASS,
        render: (company) => <span className="text-desk-muted">{company.contact}</span>,
    },
    {
        key: "currency",
        header: "Billing",
        className: CELL_CLASS,
        render: (company) => (
            <span className="rounded-full bg-desk-blue-soft px-2 py-0.5 text-xs font-semibold text-desk-blue">
                {company.billing_currency}
            </span>
        ),
    },
    {
        key: "registered",
        header: "Registered",
        className: CELL_CLASS,
        render: (company) => (
            <span className="text-xs text-desk-muted">{formatDate(company.registered_on)}</span>
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
                rowClassName={() => "border-desk-border"}
                className="[&_th]:bg-desk-surface [&_th]:text-[13px] [&_th]:text-desk-ink"
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

    useEffect(() => {
        let active = true;

        Promise.all([
            getSalesDeskSummary(DEFAULT_DESK_CURRENCY),
            getPipelineByStage(DEFAULT_DESK_CURRENCY),
            getBookingsTrend(DEFAULT_DESK_CURRENCY),
            getRepQuotaProgress(DEFAULT_DESK_CURRENCY),
            getRecentCompanies(RECENT_COMPANY_LIMIT),
        ])
            .then(([summary, stages, bookings, reps, companies]) => {
                if (active) setData({ summary, stages, bookings, reps, companies });
            })
            .catch((err) => {
                if (active)
                    setError(err instanceof Error ? err.message : "Failed to load the sales desk");
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, []);

    return (
        <div className="flex flex-col gap-6">
            <h1 className="text-2xl leading-8 font-bold text-desk-ink">Dashboard</h1>

            {error && (
                <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    {error}
                </div>
            )}

            {isLoading && <LoadingState message="Loading the sales desk..." className="h-64" />}

            {data && (
                <>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                        <DeskKpiCard
                            label="Pipeline (weighted)"
                            value={formatDeskMoney(data.summary.weighted_pipeline)}
                            caption={plural(data.summary.open_deal_count, "open deal")}
                            tone="forecast"
                        />
                        <DeskKpiCard
                            label="Total ARR pipeline"
                            value={formatDeskMoney(data.summary.total_pipeline)}
                            caption="Unweighted"
                        />
                        <DeskKpiCard
                            label="Won this period"
                            value={formatDeskMoney(data.summary.won_amount)}
                            caption={`${plural(data.summary.won_deal_count, "deal")} closed`}
                            tone="won"
                        />
                        <DeskKpiCard
                            label="Lost this period"
                            value={formatDeskMoney(data.summary.lost_amount)}
                            caption={`${plural(data.summary.lost_deal_count, "deal")} lost`}
                            tone="lost"
                        />
                    </div>

                    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                        <DeskPanel title="Bookings — 12 months">
                            <BookingsTrendChart
                                data={data.bookings}
                                currency={data.summary.currency}
                            />
                        </DeskPanel>
                        <PipelineByStagePanel stages={data.stages} />
                    </div>

                    <RepQuotaPanel reps={data.reps} />

                    <RecentCompaniesPanel companies={data.companies} />
                </>
            )}
        </div>
    );
}
