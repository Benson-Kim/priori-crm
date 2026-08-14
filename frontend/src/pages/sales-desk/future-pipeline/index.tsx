/**
 * Future pipeline, waiting deals as cards.
 *
 * Both kinds of waiting live here — parked deals (already in the book, with a
 * recorded stage to resume at) and nurture prospects (real but not yet
 * workable). Each card carries the reason we are waiting and the date the
 * waiting ends; cards rather than rows because the note is the point, and a
 * table cell would truncate it. Ordered soonest first, so anything overdue or
 * due today sits at the top. These are the same two groups the sidebar badge
 * counts, so the page always agrees with its own badge.
 */

import { ArrowRight, CalendarDays, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { formatDate, plural } from "@/lib/utils";
import {
    formatDeskMoney,
    getFuturePipelineSummary,
    getParkedDeals,
    getProspects,
    resumeDeal,
    type FuturePipelineSummary,
    type ParkedDealRow,
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

/*
 * A parked deal is not worked in a prospect table — its one action is the
 * resume control, so unlike ProspectCard the card is not a link. Resuming
 * returns the deal to the stage it was parked at and follows it into the
 * pipeline workspace, the same hand-off the engage flow makes.
 */
function ParkedDealCard({
    deal,
    isResuming,
    onResume,
}: Readonly<{ deal: ParkedDealRow; isResuming: boolean; onResume: () => void }>) {
    return (
        <div className="flex flex-col rounded-2xl border border-sd-border bg-sd-card p-5 shadow-[0_1px_3px_rgba(0,0,0,0.06)]">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <h2 className="text-[15px] font-bold text-sd-ink">{deal.company}</h2>
                    <p className="pt-0.5 text-xs text-sd-muted">
                        {deal.product} · {plural(deal.seats, "seat")}
                    </p>
                </div>
                <DueBadge
                    prospect={{
                        urgency: deal.urgency,
                        days_until: deal.days_until,
                        engage_on: deal.parked_until,
                    }}
                />
            </div>

            <p className="py-4 text-[13px] leading-relaxed text-sd-ink">{deal.note}</p>

            <div className="mt-auto flex items-end justify-between gap-3 border-t border-sd-border pt-3">
                <span className="flex items-center gap-2">
                    <Avatar
                        name={deal.owner_name}
                        initials={deal.owner_initials}
                        color={deal.owner_color}
                    />
                    <span className="text-[13px] text-sd-ink">{deal.owner_name}</span>
                </span>
                <span className="text-right">
                    <span className="block text-[11px] text-sd-muted">Value / yr</span>
                    <span className="block text-[15px] font-bold text-sd-ink">
                        {formatDeskMoney(deal.value, deal.billing_currency)}
                    </span>
                </span>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 pt-3">
                <p className="flex items-center gap-1.5 text-xs text-sd-muted">
                    <CalendarDays className="size-3.5 shrink-0" aria-hidden="true" />
                    Re-engage on {formatDate(deal.parked_until)} · resumes at{" "}
                    {deal.resume_stage_label}
                </p>
                <Button
                    variant="primary"
                    loading={isResuming}
                    onClick={onResume}
                    aria-label={`Resume the ${deal.company} deal at ${deal.resume_stage_label}`}
                >
                    Resume deal <ArrowRight size={16} />
                </Button>
            </div>
        </div>
    );
}

export default function SalesDeskFuturePipelinePage() {
    const navigate = useNavigate();

    // ?rep= scopes both the cards and the summary line to one owner, the
    // same URL-driven mechanism the pipeline workspace uses for ?deal=.
    const [searchParams] = useSearchParams();
    const repFilter = searchParams.get("rep") ?? undefined;

    const [prospects, setProspects] = useState<ProspectRow[]>([]);
    const [parkedDeals, setParkedDeals] = useState<ParkedDealRow[]>([]);
    const [summary, setSummary] = useState<FuturePipelineSummary | null>(null);
    const [isCreating, setIsCreating] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);

    // The deal a resume call is in flight for, so only its button spins.
    const [resumingId, setResumingId] = useState<string | null>(null);

    const dismissNotice = useCallback(() => setNotice(null), []);

    useEffect(() => {
        let active = true;

        Promise.all([
            getProspects(undefined, repFilter),
            getParkedDeals(repFilter),
            getFuturePipelineSummary(repFilter),
        ])
            .then(([rows, parked, next]) => {
                if (!active) return;
                setProspects(rows);
                setParkedDeals(parked);
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

    /**
     * Resume the parked deal at its recorded stage, then follow it into the
     * pipeline workspace — the same hand-off engaging a prospect makes.
     */
    const resumeParkedDeal = useCallback(
        async (deal: ParkedDealRow) => {
            setResumingId(deal.id);
            setError(null);
            try {
                await resumeDeal(deal.id);
                navigate(`/sales-desk/pipeline/workspace?deal=${deal.id}`);
            } catch (err) {
                setError(
                    err instanceof Error ? err.message : "Could not resume that deal."
                );
                setResumingId(null);
                // Re-read both lists: a refused resume usually means the deal
                // is no longer parked, so the card itself is stale.
                setRevision((value) => value + 1);
            }
        },
        [navigate]
    );

    const dueParkedCount = parkedDeals.filter(
        (deal) => deal.urgency !== "scheduled"
    ).length;

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
                <LoadingState message="Loading the future pipeline..." className="h-64" />
            ) : (
                <>
                    {/*
                     * Parked deals lead: they are already-won attention with a
                     * resume date, and the sidebar badge counts the due ones,
                     * so hiding them here made the page disagree with its own
                     * badge (finding 03).
                     */}
                    {parkedDeals.length > 0 && (
                        <section className="flex flex-col gap-3" aria-label="Parked deals">
                            <div className="flex items-center gap-2">
                                <h2 className="text-[13px] font-semibold text-sd-ink">
                                    Parked deals
                                </h2>
                                <span className="rounded-full bg-sd-brand-bg px-2 py-0.5 text-xs font-bold text-sd-brand">
                                    {parkedDeals.length}
                                </span>
                                {dueParkedCount > 0 && (
                                    <span className="rounded-full bg-sd-danger-bg px-2.5 py-0.5 text-xs font-semibold text-sd-danger">
                                        {dueParkedCount} due now
                                    </span>
                                )}
                            </div>
                            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                                {parkedDeals.map((deal) => (
                                    <ParkedDealCard
                                        key={deal.id}
                                        deal={deal}
                                        isResuming={resumingId === deal.id}
                                        onResume={() => void resumeParkedDeal(deal)}
                                    />
                                ))}
                            </div>
                        </section>
                    )}

                    <section className="flex flex-col gap-3" aria-label="Nurture prospects">
                        {parkedDeals.length > 0 && (
                            <h2 className="text-[13px] font-semibold text-sd-ink">
                                Nurture prospects
                            </h2>
                        )}
                        {prospects.length === 0 ? (
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
                    </section>
                </>
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
