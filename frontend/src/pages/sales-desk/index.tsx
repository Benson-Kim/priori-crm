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
import { Avatar } from "@/components/ui/Avatar";
import { KpiCard } from "@/components/ui/KpiCard";
import { DeskPanel } from "./components/DeskPanel";
import { ProgressBar } from "@/components/ui/ProgressBar";

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
                            <span className="text-xs font-medium text-sd-ink">{line.stage}</span>
                            <span className="text-xs text-sd-muted">
                                {formatDeskMoney(line.amount)} &middot; {plural(line.deal_count, "deal")}
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

// Section: each rep's weighted pipeline against quota

function RepQuotaPanel({ reps }: Readonly<{ reps: RepQuotaLine[] }>) {
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
                                    {formatDeskMoney(rep.weighted_pipeline)} /{" "}
                                    {formatDeskMoney(rep.quarter_target)}
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

const CELL_CLASS = "px-5 py-3 text-[13px]";

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
            <span className="text-xs text-sd-muted">{formatDate(company.registered_on)}</span>
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
                className="[&_th]:bg-sd-card"
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
            <h1 className="text-2xl leading-8 font-bold text-sd-ink">Dashboard</h1>

            {error && (
                <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    {error}
                </div>
            )}

            {isLoading && <LoadingState message="Loading the sales desk..." className="h-64" />}

            {data && (
                <>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                        <KpiCard
                            label="Pipeline (weighted)"
                            value={formatDeskMoney(data.summary.weighted_pipeline)}
                            subline={plural(data.summary.open_deal_count, "open deal")}
                            tone="info"
                        />
                        <KpiCard
                            label="Total ARR pipeline"
                            value={formatDeskMoney(data.summary.total_pipeline)}
                            subline="Unweighted"
                        />
                        <KpiCard
                            label="Won this period"
                            value={formatDeskMoney(data.summary.won_amount)}
                            subline={`${plural(data.summary.won_deal_count, "deal")} closed`}
                            tone="success"
                        />
                        <KpiCard
                            label="Lost this period"
                            value={formatDeskMoney(data.summary.lost_amount)}
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
                        <PipelineByStagePanel stages={data.stages} />
                    </div>

                    <RepQuotaPanel reps={data.reps} />

                    <RecentCompaniesPanel companies={data.companies} />
                </>
            )}
        </div>
    );
}
