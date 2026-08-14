/**
 * Pipeline workspace.
 *
 * Three layers, each narrowing the one below: pick an owner, read where their
 * book sits by stage, then filter rows by how much attention each deal is
 * owed. Selecting a row opens the drawer, the only place a deal changes.
 *
 * The whole view state lives in the query string — selected deal, owner,
 * activity chip, closed toggle and search — so a filtered view is shareable,
 * survives reload, and the back button steps out of the drawer.
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
import { cn, formatDate, plural, saveBlob } from "@/lib/utils";
import {
    advanceDeal,
    closeDeal,
    exportPipelineCsv,
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

/** Runtime list of the activity chips, for validating the URL param. */
const ACTIVITY_KEYS: readonly ActivityFilterKey[] = [
    "all",
    "active_this_week",
    "quiet_8_30",
    "no_activity_30",
    "open_45",
];

const isActivityKey = (value: string | null): value is ActivityFilterKey =>
    ACTIVITY_KEYS.includes(value as ActivityFilterKey);

export default function SalesDeskPipelineWorkspacePage() {
    const [searchParams, setSearchParams] = useSearchParams();
    const selectedDealId = searchParams.get("deal") || null;

    /*
     * Filter state is read straight from the URL (issue #47 scope 6), with
     * defaults left out of the query string so a pristine view has a clean
     * address. An unrecognised activity value degrades to "all" rather than
     * breaking a stale shared link.
     */
    const ownerId = searchParams.get("owner");
    const activityParam = searchParams.get("activity");
    const activity: ActivityFilterKey = isActivityKey(activityParam) ? activityParam : "all";
    const showClosed = searchParams.get("closed") !== "0";
    const search = searchParams.get("q") ?? "";
    const debouncedSearch = useDebounce(search, 250);

    /**
     * Write one view param, dropping it at its default. Filter changes tune
     * the current view rather than navigating, so they replace instead of
     * stacking a history entry per keystroke.
     */
    const setViewParam = useCallback(
        (key: string, value: string | null) => {
            setSearchParams(
                (params) => {
                    if (value === null) params.delete(key);
                    else params.set(key, value);
                    return params;
                },
                { replace: true }
            );
        },
        [setSearchParams]
    );

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
        (dealId: string | null) => {
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
                                        ? "text-sd-brand"
                                        : "text-sd-ink"
                                )}
                            >
                                {deal.company_name}
                            </span>
                            <span className="block text-[11px] text-sd-muted">
                                {deal.product} · {plural(deal.seats, "seat")} · {deal.billing_currency}
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
                        {formatDeskMoneyCompact(deal.value, deal.billing_currency)}
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
                            {formatDate(deal.latest_record.logged_on, "table")}
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
                        <span className="text-sd-brand">{openPipelineLabel}</span>
                    </span>
                    <Button
                        variant="outline-secondary"
                        className="h-control"
                        onClick={() =>
                            // TODO(#45-wiring): swaps to the server export via
                            // GET /sales-desk/exports/pipeline inside
                            // exportPipelineCsv; this call site is final.
                            void exportPipelineCsv({
                                ownerId: ownerId ?? undefined,
                                activity,
                                includeClosed: showClosed,
                                search: debouncedSearch,
                            }).then((blob) => saveBlob(blob, "pipeline.csv"))
                        }
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
                            onSelect={(id) => setViewParam("owner", id)}
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
                            onTabChange={(key) =>
                                setViewParam("activity", key === "all" ? null : key)
                            }
                        />
                    )}

                    <Checkbox
                        label="Show closed"
                        checked={showClosed}
                        onChange={(event) =>
                            setViewParam("closed", event.target.checked ? null : "0")
                        }
                        className="ml-auto shrink-0"
                    />

                    <SearchInput
                        value={search}
                        onSearchChange={(value) => setViewParam("q", value || null)}
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
                                selectedDealId ?? undefined
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
                        /*
                         * Parking goes through the same afterWrite re-read
                         * closing uses (finding 08: the stage strip and rep
                         * cards kept pre-park figures). The refresh is issued
                         * before the drawer-closing navigation rather than
                         * behind it, so the scoreboard re-fetch can never be
                         * lost to the URL transition that unmounts the drawer.
                         */
                        await afterWrite(moveDealToFuturePipeline(detail.deal.id, note));
                        selectDeal(null);
                    }}
                />
            )}
        </>
    );
}
