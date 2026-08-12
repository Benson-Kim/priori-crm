/**
 * Pipeline workspace.
 *
 * Three layers, each narrowing the one below: pick an owner, read where their
 * book sits by stage, then filter rows by how much attention each deal is
 * owed. Selecting a row opens the drawer, the only place a deal changes.
 *
 * The selected deal lives in the query string, so a deal is linkable and the
 * back button steps out of the drawer.
 */

import { Download } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table, type Column } from "@/components/ui/Table";
import { useDebounce } from "@/hooks/useDebounce";
import { cn, formatDate, saveBlob } from "@/lib/utils";
import {
    advanceDeal,
    closeDeal,
    formatDeskMoneyCompact,
    getDealDetail,
    getPipelineDeals,
    getPipelineOverview,
    logDealActivity,
    moveDealToFuturePipeline,
    type ActivityFilterKey,
    type DealDetail,
    type PipelineDeal,
    type PipelineOverview,
} from "@/services/salesDeskApi";
import { Avatar } from "@/components/ui/Avatar";
import { AgeRibbon } from "@/components/ui/AgeRibbon";
import { DealDrawer } from "../components/DealDrawer";
import { RepFilterCards } from "../components/RepFilterCards";
import { StagePath } from "@/components/ui/StagePath";
import { StageSummaryStrip } from "../components/StageSummaryStrip";

/**
 * Desk-density overrides for the shared Table's roomier defaults. A column's
 * `className` lands on both its `th` and its `td`, so header-only tweaks ride
 * on the table wrapper instead.
 */
const CELL_CLASS = "align-top";
/*
 * Every cell carries two lines, so columns are sized to the design's
 * proportions and the table scrolls horizontally below that width rather than
 * wrapping each cell to three lines.
 */
const TABLE_OVERRIDES =
    "[&_th]:py-3 [&_table]:min-w-[1240px]";

/** Escape a CSV field: wrap in quotes and double any embedded quote. */
const csvField = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;

function exportDealsCsv(deals: PipelineDeal[]) {
    const header = [
        "Company",
        "Product",
        "Seats",
        "Value",
        "Weighted",
        "Billing currency",
        "Stage",
        "Status",
        "Owner",
        "Days in pipeline",
        "Days idle",
        "Latest record",
    ];
    const rows = deals.map((deal) => [
        deal.company_name,
        deal.product,
        deal.seats,
        Math.round(deal.value),
        deal.weighted_value === null ? "" : Math.round(deal.weighted_value),
        deal.billing_currency,
        deal.stage_label,
        deal.status,
        deal.owner_name,
        deal.age_days,
        deal.idle_days,
        deal.latest_record.note,
    ]);
    const csv = [header, ...rows].map((row) => row.map(csvField).join(",")).join("\n");
    saveBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "pipeline.csv");
}

export default function SalesDeskPipelineWorkspacePage() {
    const [searchParams, setSearchParams] = useSearchParams();
    const selectedDealId = Number(searchParams.get("deal")) || null;

    const [ownerId, setOwnerId] = useState<string | null>(null);
    const [activity, setActivity] = useState<ActivityFilterKey>("all");
    const [showClosed, setShowClosed] = useState(true);
    const [search, setSearch] = useState("");
    const debouncedSearch = useDebounce(search, 250);

    const [overview, setOverview] = useState<PipelineOverview | null>(null);
    const [deals, setDeals] = useState<PipelineDeal[]>([]);
    const [detail, setDetail] = useState<DealDetail | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Bumped after every write so the whole page re-reads the mutated store.
    const [revision, setRevision] = useState(0);
    const refresh = useCallback(() => setRevision((value) => value + 1), []);

    // Stale-response guard: only the newest load may write state.
    const seqRef = useRef(0);
    useEffect(() => {
        const seq = ++seqRef.current;
        setIsLoading(true);

        Promise.all([
            getPipelineOverview(ownerId ?? undefined),
            getPipelineDeals({
                ownerId: ownerId ?? undefined,
                activity,
                includeClosed: showClosed,
                search: debouncedSearch,
            }),
        ])
            .then(([nextOverview, nextDeals]) => {
                if (seq !== seqRef.current) return;
                setOverview(nextOverview);
                setDeals(nextDeals);
                setError(null);
            })
            .catch((err) => {
                if (seq !== seqRef.current) return;
                setError(err instanceof Error ? err.message : "Failed to load the pipeline");
            })
            .finally(() => {
                if (seq === seqRef.current) setIsLoading(false);
            });
    }, [ownerId, activity, showClosed, debouncedSearch, revision]);

    // Load the selected deal separately: the drawer must survive the table
    // re-filtering underneath it.
    useEffect(() => {
        if (selectedDealId === null) {
            setDetail(null);
            return;
        }
        let active = true;
        getDealDetail(selectedDealId)
            .then((next) => {
                if (active) setDetail(next);
            })
            .catch(() => {
                // The deal is gone (moved to the future pipeline), drop the
                // selection rather than stranding an empty drawer.
                if (active) setDetail(null);
            });
        return () => {
            active = false;
        };
    }, [selectedDealId, revision]);

    const selectDeal = useCallback(
        (dealId: number | null) => {
            setSearchParams(
                (params) => {
                    if (dealId === null) params.delete("deal");
                    else params.set("deal", String(dealId));
                    return params;
                },
                /*
                 * Opening pushes, so Back closes the drawer instead of leaving
                 * the workspace; closing replaces, so Back does not reopen what
                 * the user just dismissed.
                 */
                { replace: dealId === null }
            );
        },
        [setSearchParams]
    );

    /** Run a write, then re-read the page from the mutated store. */
    const afterWrite = useCallback(
        async (action: Promise<unknown>) => {
            await action;
            refresh();
        },
        [refresh]
    );

    const columns = useMemo<Column<PipelineDeal>[]>(
        () => [
            {
                key: "deal",
                header: "Deal",
                className: `${CELL_CLASS} w-[340px]`,
                render: (deal) => (
                    <span className="flex items-start gap-2.5">
                        <Avatar name={deal.owner_name} initials={deal.owner_initials} color={deal.owner_color} />
                        <span className="min-w-0">
                            <span
                                className={cn(
                                    "block text-[13px] font-semibold",
                                    deal.id === selectedDealId
                                        ? "text-priori-purple"
                                        : "text-sd-ink"
                                )}
                            >
                                {deal.company_name}
                            </span>
                            <span className="block text-[11px] text-sd-muted">
                                {deal.product} · {deal.seats} seats · {deal.billing_currency}
                            </span>
                        </span>
                    </span>
                ),
            },
            {
                key: "time",
                header: "Time in pipeline",
                className: `${CELL_CLASS} w-[200px]`,
                render: (deal) => (
                    <AgeRibbon
                        ageDays={deal.age_days}
                        idleDays={deal.status === "open" ? deal.idle_days : undefined}
                        closed={deal.status !== "open"}
                    />
                ),
            },
            {
                key: "value",
                header: "Value / yr",
                className: `${CELL_CLASS} w-[110px]`,
                render: (deal) => (
                    <span className="text-[13px] font-bold text-sd-ink">
                        {formatDeskMoneyCompact(deal.value)}
                    </span>
                ),
            },
            {
                key: "progress",
                header: "Progress",
                className: `${CELL_CLASS} w-[290px]`,
                render: (deal) => (
                    <StagePath
                        stageIndex={deal.stage_index}
                        status={deal.status}
                        stageLabel={deal.stage_label}
                        closeReason={deal.close_reason}
                    />
                ),
            },
            {
                key: "record",
                header: "Latest record",
                className: `${CELL_CLASS} w-[300px]`,
                render: (deal) => (
                    <>
                        <p className="line-clamp-2 text-xs leading-relaxed text-sd-ink">
                            {deal.latest_record.note}
                        </p>
                        <p className="pt-1 text-[11px] text-sd-muted">
                            {deal.latest_record.stage} ·{" "}
                            {formatDate(deal.latest_record.logged_on)}
                        </p>
                    </>
                ),
            },
        ],
        [selectedDealId]
    );

    const openPipelineLabel = overview
        ? `${formatDeskMoneyCompact(overview.open_pipeline_value, overview.currency)}/yr`
        : "—";

    return (
        // The drawer overlays the page, so nothing here reflows on selection.
        <>
            <div className="flex min-w-0 flex-col gap-4">
                <div className="flex flex-wrap items-center justify-end gap-3">
                    <span className="text-[13px] font-semibold text-sd-ink">
                        Open pipeline{" "}
                        <span className="text-priori-purple">{openPipelineLabel}</span>
                    </span>
                    <Button
                        variant="outline-secondary"
                        className="h-control"
                        onClick={() => exportDealsCsv(deals)}
                    >
                        <Download size={16} /> Export CSV
                    </Button>
                </div>

                {error && (
                    <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                        {error}
                    </div>
                )}

                {overview && (
                    <>
                        <RepFilterCards
                            overview={overview}
                            selectedOwnerId={ownerId}
                            onSelect={setOwnerId}
                        />
                        <StageSummaryStrip overview={overview} />
                    </>
                )}

                {/*
                 * The toolbar reads against the page, not inside the table
                 * card, and stays on one line once there is room for it.
                 */}
                <div className="flex flex-wrap items-center gap-3 xl:flex-nowrap">
                    {overview && (
                        <FilterTabs
                                variant="brand-outline"
                                tabs={overview.filters.map((filter) => ({
                                    key: filter.key,
                                    label: filter.label,
                                    count: filter.count,
                                }))}
                            activeTab={activity}
                            onTabChange={(key) => setActivity(key as ActivityFilterKey)}
                        />
                    )}

                    <Checkbox
                        label="Show closed"
                        checked={showClosed}
                        onChange={(event) => setShowClosed(event.target.checked)}
                        className="ml-auto shrink-0"
                    />

                    <SearchInput
                        value={search}
                        onSearchChange={setSearch}
                        aria-label="Search deals"
                        placeholder="Search deals…"
                        className="h-control w-56 shrink-0 bg-sd-card px-3 [&_svg]:size-4"
                    />

                </div>

                <div className="shrink-0 overflow-hidden rounded-2xl border border-sd-border bg-sd-card shadow-sd-card">
                    {isLoading && !overview ? (
                        <LoadingState message="Loading the pipeline..." className="h-64" />
                    ) : (
                        <Table
                            columns={columns}
                            data={deals}
                            rowKey={(deal) => String(deal.id)}
                            onRowClick={(deal) => selectDeal(deal.id)}
                            variant="sales-desk"
                            className={TABLE_OVERRIDES}
                            selectedKey={
                                selectedDealId === null ? undefined : String(selectedDealId)
                            }
                            emptyMessage="No deals match these filters."
                        />
                    )}
                </div>

            </div>

            {/*
             * Gated on the id as well as the record: while a different deal
             * loads, `detail` still holds the previous one. Rendering it would
             * show the old deal with the new row highlighted, and its actions
             * would write to whichever deal the callbacks name.
             */}
            {detail && detail.deal.id === selectedDealId && (
                <DealDrawer
                    // Remount on a different deal so a note typed against one
                    // deal can never be submitted against another.
                    key={detail.deal.id}
                    detail={detail}
                    onClose={() => selectDeal(null)}
                    onLogActivity={(note) => afterWrite(logDealActivity(detail.deal.id, note))}
                    onAdvance={(note) => afterWrite(advanceDeal(detail.deal.id, note))}
                    onCloseDeal={(outcome, reason, note) =>
                        afterWrite(closeDeal(detail.deal.id, outcome, reason, note))
                    }
                    onMoveToFuture={async (note) => {
                        await moveDealToFuturePipeline(detail.deal.id, note);
                        selectDeal(null);
                        refresh();
                    }}
                />
            )}
        </>
    );
}
