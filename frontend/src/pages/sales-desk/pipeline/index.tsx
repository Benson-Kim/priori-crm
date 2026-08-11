/**
 * Pipeline, compact view.
 *
 * One row per deal with full annual and weighted values, filtered by status.
 * It answers what is in the book and what it is worth, without the activity
 * signals and workflow controls of the workspace. Following a row opens that
 * deal in the workspace.
 */

import { ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Table, type Column } from "@/components/ui/Table";
import {
    formatDeskMoney,
    getPipelineDeals,
    type DealStatus,
    type PipelineDeal,
} from "@/services/salesDeskApi";
import { RepAvatar } from "../components/RepAvatar";
import { StagePill } from "./components/StagePill";

/** Tab keys are strings because FilterTabs is key-agnostic. */
type StatusTabKey = "all" | DealStatus;

const STATUS_TAB_LABELS: Array<{ key: StatusTabKey; label: string }> = [
    { key: "all", label: "All" },
    { key: "open", label: "Open" },
    { key: "won", label: "Won" },
    { key: "lost", label: "Lost" },
];

/** First name only, since the column is narrow and the avatar disambiguates. */
const firstNameOf = (name: string) => name.split(" ")[0];

/*
 * Desk-density overrides for the shared Table. A column's `className` lands on
 * both its `th` and its `td`, so header-only tweaks ride on the wrapper.
 */
const CELL_CLASS = "px-5 py-3 text-[13px]";
const HEADER_OVERRIDES = "[&_th]:bg-desk-bg [&_th]:text-[13px] [&_th]:text-desk-ink";

export default function SalesDeskPipelinePage() {
    const navigate = useNavigate();

    const [deals, setDeals] = useState<PipelineDeal[]>([]);
    const [tab, setTab] = useState<StatusTabKey>("all");
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;

        getPipelineDeals()
            .then((rows) => {
                if (active) setDeals(rows);
            })
            .catch((err) => {
                if (active)
                    setError(err instanceof Error ? err.message : "Failed to load the pipeline");
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, []);

    const openInWorkspace = (deal: PipelineDeal) =>
        navigate(`/sales-desk/pipeline/workspace?deal=${deal.id}`);

    const tabs = STATUS_TAB_LABELS.map((entry) => ({
        ...entry,
        count:
            entry.key === "all"
                ? deals.length
                : deals.filter((deal) => deal.status === entry.key).length,
    }));

    const rows = deals.filter((deal) => (tab === "all" ? true : deal.status === tab));

    const columns = useMemo<Column<PipelineDeal>[]>(
        () => [
            {
                key: "company",
                header: "Company",
                className: CELL_CLASS,
                render: (deal) => (
                    <span className="font-medium text-desk-ink">{deal.company_name}</span>
                ),
            },
            {
                key: "product",
                header: "Product",
                className: CELL_CLASS,
                render: (deal) => <span className="text-desk-muted">{deal.product}</span>,
            },
            {
                key: "seats",
                header: "Seats",
                className: CELL_CLASS,
                render: (deal) => <span className="text-desk-ink">{deal.seats}</span>,
            },
            {
                key: "value",
                header: "Value (ARR)",
                className: CELL_CLASS,
                render: (deal) => (
                    <span className="font-medium text-desk-ink">
                        {formatDeskMoney(deal.value)}
                    </span>
                ),
            },
            {
                key: "weighted",
                header: "Weighted",
                className: CELL_CLASS,
                render: (deal) => (
                    <span className="text-desk-muted">
                        {deal.weighted_value === null
                            ? "—"
                            : formatDeskMoney(deal.weighted_value)}
                    </span>
                ),
            },
            {
                key: "stage",
                header: "Stage",
                className: CELL_CLASS,
                render: (deal) => <StagePill deal={deal} />,
            },
            {
                key: "owner",
                header: "Owner",
                className: CELL_CLASS,
                render: (deal) => (
                    <span className="flex items-center gap-2">
                        <RepAvatar
                            initials={deal.owner_initials}
                            color={deal.owner_color}
                            size="sm"
                        />
                        <span className="text-xs text-desk-muted">
                            {firstNameOf(deal.owner_name)}
                        </span>
                    </span>
                ),
            },
            {
                key: "open",
                header: "",
                className: `${CELL_CLASS} text-right w-12`,
                render: (deal) => (
                    <ChevronRight
                        className="ml-auto size-4 text-desk-muted"
                        aria-label={`Open ${deal.company_name} in the workspace`}
                    />
                ),
            },
        ],
        []
    );

    return (
        <div className="flex flex-col gap-5">
            <h1 className="text-2xl leading-8 font-bold text-desk-ink">Pipeline</h1>

            <FilterTabs
                variant="segmented"
                tabs={tabs}
                activeTab={tab}
                onTabChange={(key) => setTab(key as StatusTabKey)}
            />

            {error && (
                <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    {error}
                </div>
            )}

            {isLoading ? (
                <LoadingState message="Loading the pipeline..." className="h-64" />
            ) : (
                <Table
                    columns={columns}
                    data={rows}
                    rowKey={(deal) => String(deal.id)}
                    onRowClick={openInWorkspace}
                    className={`overflow-hidden rounded-2xl border border-desk-border bg-desk-surface shadow-[0_1px_3px_rgba(0,0,0,0.06)] ${HEADER_OVERRIDES}`}
                    rowClassName={() => "border-desk-border hover:bg-desk-bg"}
                    emptyMessage="No deals match this filter."
                />
            )}
        </div>
    );
}
