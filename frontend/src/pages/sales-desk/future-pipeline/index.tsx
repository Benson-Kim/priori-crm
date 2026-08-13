/**
 * Future pipeline, nurture cards.
 *
 * Prospects that are real but not yet workable: the reason we are waiting and
 * the date the waiting ends. Cards rather than rows because the note is the
 * point, and a table cell would truncate it. Ordered soonest first, so
 * anything overdue or due today sits at the top.
 */

import { CalendarDays, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { formatDate } from "@/lib/utils";
import {
    formatDeskMoney,
    getFuturePipelineSummary,
    getProspects,
    type FuturePipelineSummary,
    type ProspectRow,
} from "@/services/salesDeskApi";
import { Avatar } from "@/components/ui/Avatar";
import { AddProspectDialog } from "./components/AddProspectDialog";
import { DueBadge } from "./components/DueBadge";
import { SuccessNotice } from "./components/SuccessNotice";

/*
 * The whole card links through to the prospect table, matching how a row opens
 * the workspace on the other screens, so no extra control is needed here.
 */
function ProspectCard({ prospect }: Readonly<{ prospect: ProspectRow }>) {
    return (
        <Link
            to="/sales-desk/future-pipeline/workspace"
            aria-label={`Work ${prospect.company} in the prospect table`}
            className="flex flex-col rounded-2xl border border-sd-border bg-sd-card p-5 shadow-[0_1px_3px_rgba(0,0,0,0.06)] transition-colors hover:border-sd-muted"
        >
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <h2 className="text-[15px] font-bold text-sd-ink">{prospect.company}</h2>
                    <p className="pt-0.5 text-xs text-sd-muted">{prospect.contact}</p>
                </div>
                <DueBadge prospect={prospect} />
            </div>

            <p className="py-4 text-[13px] leading-relaxed text-sd-ink">{prospect.note}</p>

            <div className="mt-auto flex items-end justify-between gap-3 border-t border-sd-border pt-3">
                <span className="flex items-center gap-2">
                    <Avatar
                        name={prospect.owner_name}
                        initials={prospect.owner_initials}
                        color={prospect.owner_color}
                    />
                    <span className="text-[13px] text-sd-ink">{prospect.owner_name}</span>
                </span>
                <span className="text-right">
                    <span className="block text-[11px] text-sd-muted">Est. ARR</span>
                    <span className="block text-[15px] font-bold text-sd-ink">
                        {formatDeskMoney(prospect.estimated_arr)}
                    </span>
                </span>
            </div>

            <p className="flex items-center gap-1.5 pt-3 text-xs text-sd-muted">
                <CalendarDays className="size-3.5 shrink-0" aria-hidden="true" />
                Engage on {formatDate(prospect.engage_on)}
            </p>
        </Link>
    );
}

export default function SalesDeskFuturePipelinePage() {
    // ?rep= scopes both the cards and the summary line to one owner, the
    // same URL-driven mechanism the pipeline workspace uses for ?deal=.
    const [searchParams] = useSearchParams();
    const repFilter = searchParams.get("rep") ?? undefined;

    const [prospects, setProspects] = useState<ProspectRow[]>([]);
    const [summary, setSummary] = useState<FuturePipelineSummary | null>(null);
    const [isCreating, setIsCreating] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);

    const dismissNotice = useCallback(() => setNotice(null), []);

    useEffect(() => {
        let active = true;

        Promise.all([
            getProspects(undefined, undefined, repFilter),
            getFuturePipelineSummary(undefined, repFilter),
        ])
            .then(([rows, next]) => {
                if (!active) return;
                setProspects(rows);
                setSummary(next);
                setError(null);
            })
            .catch((err) => {
                if (active)
                    setError(
                        err instanceof Error ? err.message : "Failed to load the future pipeline"
                    );
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, [repFilter, revision]);

    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                {/* A live summary of the list, not the page description. */}
                <div>
                    <p className="text-[13px] text-sd-muted">
                        {summary
                            ? `Nurture list · ${summary.prospect_count} ${
                                  summary.prospect_count === 1 ? "company" : "companies"
                              } · est. ${formatDeskMoney(
                                  summary.total_estimated_arr,
                                  summary.currency
                              )} potential ARR`
                            : "Nurture list"}
                    </p>
                </div>

                <Button variant="primary" onClick={() => setIsCreating(true)}>
                    <Plus size={16} /> Add planned deal
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

            {notice && <SuccessNotice message={notice} onDismiss={dismissNotice} />}

            {isLoading ? (
                <LoadingState message="Loading the nurture list..." className="h-64" />
            ) : prospects.length === 0 ? (
                <p className="rounded-2xl border border-sd-border bg-sd-card p-10 text-center text-[13px] text-sd-muted">
                    No prospects planned.
                </p>
            ) : (
                <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                    {prospects.map((prospect) => (
                        <ProspectCard key={prospect.id} prospect={prospect} />
                    ))}
                </div>
            )}

            <AddProspectDialog
                isOpen={isCreating}
                onClose={() => setIsCreating(false)}
                onCreated={() => {
                    setIsCreating(false);
                    setNotice("Planned deal added — you'll be notified when it's due");
                    setRevision((value) => value + 1);
                }}
            />
        </div>
    );
}
