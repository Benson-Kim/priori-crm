/**
 * Pipeline, compact view.
 *
 * One row per deal with full annual and weighted values, filtered by status.
 * It answers what is in the book and what it is worth, without the activity
 * signals and workflow controls of the workspace. Following a row opens that
 * deal in the workspace.
 */

import { Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { StageChip } from "@/components/ui/Chip";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Table, type Column } from "@/components/ui/Table";
import { AddDealDialog } from "./components/AddDealDialog";
import {
    formatDeskMoney,
    getPipelineDeals,
    type DealStatus,
    type PipelineDeal,
} from "@/services/salesDeskApi";

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
const CELL_CLASS = "";

export default function SalesDeskPipelinePage() {
    const navigate = useNavigate();

    const [deals, setDeals] = useState<PipelineDeal[]>([]);
    const [tab, setTab] = useState<StatusTabKey>("all");
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [isAddingDeal, setIsAddingDeal] = useState(false);

    // Bumped after a deal is added so the list re-reads from the server.
    const [revision, setRevision] = useState(0);
    const refresh = useCallback(() => setRevision((value) => value + 1), []);

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
    }, [revision]);

    /*
     * A row leads to the workspace, where deals are actually worked. The deal
     * is not carried across: opening a drawer the user has not asked for hides
     * the list they arrived to read, so the workspace opens on its own list and
     * they pick a row there.
     */
    const openInWorkspace = () => navigate("/sales-desk/pipeline/workspace");

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
                    <span className="font-medium text-sd-ink">{deal.company_name}</span>
                ),
            },
            {
                key: "product",
                header: "Product",
                className: CELL_CLASS,
                render: (deal) => <span className="text-sd-muted">{deal.product}</span>,
            },
            {
                key: "seats",
                header: "Seats",
                className: CELL_CLASS,
                render: (deal) => <span className="text-sd-ink">{deal.seats}</span>,
            },
            {
                key: "value",
                header: "Value (ARR)",
                className: CELL_CLASS,
                render: (deal) => (
                    <span className="font-medium text-sd-ink">
                        {formatDeskMoney(deal.value, deal.billing_currency)}
                    </span>
                ),
            },
            {
                key: "weighted",
                header: "Weighted",
                className: CELL_CLASS,
                render: (deal) => (
                    <span className="text-sd-muted">
                        {deal.weighted_value === null
                            ? "—"
                            : formatDeskMoney(deal.weighted_value, deal.billing_currency)}
                    </span>
                ),
            },
            {
                key: "stage",
                header: "Stage",
                className: CELL_CLASS,
                render: (deal) => <StageChip label={deal.stage_label} />,
            },
            {
                key: "owner",
                header: "Owner",
                className: CELL_CLASS,
                render: (deal) => (
                    <span className="flex items-center gap-2">
                        <Avatar
                            name={deal.owner_name}
                            initials={deal.owner_initials}
                            color={deal.owner_color}
                            size={24}
                        />
                        <span className="text-sd-muted">
                            {firstNameOf(deal.owner_name)}
                        </span>
                    </span>
                ),
            },
        ],
        []
    );

    return (
        <div className="flex flex-col gap-5">
            {/*
             * The status tabs and the add action share a row: this view is
             * read-first, so creating sits beside the filters rather than
             * above the table competing with them.
             */}
            <div className="flex flex-wrap items-center gap-3">
                <FilterTabs
                    variant="brand-outline"
                    tabs={tabs}
                    activeTab={tab}
                    onTabChange={(key) => setTab(key as StatusTabKey)}
                />
                <Button
                    variant="sd-primary"
                    size="sd"
                    className="ml-auto h-control"
                    onClick={() => setIsAddingDeal(true)}
                >
                    <Plus size={16} /> Add deal
                </Button>
            </div>

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
                    variant="sales-desk"
                    className="shadow-sd-card"
                    chevron={() => "Open the pipeline workspace"}
                    emptyMessage="No deals match this filter."
                />
            )}

            <AddDealDialog
                isOpen={isAddingDeal}
                onClose={() => setIsAddingDeal(false)}
                onCreated={() => {
                    setIsAddingDeal(false);
                    /*
                     * A new deal is open, so a status tab left on Won or Lost
                     * would hide it and the write would look like it failed.
                     */
                    setTab("all");
                    refresh();
                }}
            />
        </div>
    );
}
