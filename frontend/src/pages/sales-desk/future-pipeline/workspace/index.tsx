/**
 * Future pipeline, prospect table.
 *
 * The same nurture list as the cards, arranged for working through rather than
 * reading. Engaging registers the company, opens a deal at Activation carrying
 * the nurture note, and drops the prospect, so the row leaves this screen and
 * lands in the pipeline.
 */

import { ArrowRight, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table, type Column } from "@/components/ui/Table";
import { useDebounce } from "@/hooks/useDebounce";
import {
    engageProspect,
    formatDeskMoney,
    getFuturePipelineSummary,
    getProspects,
    type FuturePipelineSummary,
    type ProspectRow,
} from "@/services/salesDeskApi";
import { RepAvatar } from "../../components/RepAvatar";
import { AddProspectDialog } from "../components/AddProspectDialog";
import { DueBadge, UrgencyDot } from "../components/DueBadge";

const CELL_CLASS = "px-4 py-4 align-top text-[13px]";
const TABLE_OVERRIDES =
    "[&_th]:bg-desk-surface [&_th]:text-[13px] [&_th]:text-desk-ink [&_th]:py-3 [&_table]:min-w-[1100px]";

/** First name only, since the column is narrow and the avatar disambiguates. */
const firstNameOf = (name: string) => name.split(" ")[0];

export default function SalesDeskFuturePipelineWorkspacePage() {
    const navigate = useNavigate();

    const [prospects, setProspects] = useState<ProspectRow[]>([]);
    const [summary, setSummary] = useState<FuturePipelineSummary | null>(null);
    const [search, setSearch] = useState("");
    const debouncedSearch = useDebounce(search, 250);
    const [isCreating, setIsCreating] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);

    useEffect(() => {
        let active = true;

        Promise.all([getProspects(debouncedSearch), getFuturePipelineSummary()])
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
    }, [debouncedSearch, revision]);

    /** Promote the prospect, then follow it through to the deal it became. */
    const engage = useCallback(
        async (prospect: ProspectRow) => {
            try {
                const { dealId } = await engageProspect(prospect.id);
                navigate(`/sales-desk/pipeline/workspace?deal=${dealId}`);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Could not engage that prospect.");
                setRevision((value) => value + 1);
            }
        },
        [navigate]
    );

    const columns = useMemo<Column<ProspectRow>[]>(
        () => [
            {
                key: "company",
                header: "Company",
                className: `${CELL_CLASS} w-[220px]`,
                render: (prospect) => (
                    <span className="flex items-center gap-2">
                        <UrgencyDot urgency={prospect.urgency} />
                        <span className="font-semibold text-desk-ink">{prospect.company}</span>
                    </span>
                ),
            },
            {
                key: "contact",
                header: "Contact",
                className: `${CELL_CLASS} w-[180px]`,
                render: (prospect) => (
                    <span className="text-desk-muted">{prospect.contact || "—"}</span>
                ),
            },
            {
                key: "owner",
                header: "Owner",
                className: `${CELL_CLASS} w-[140px]`,
                render: (prospect) => (
                    <span className="flex items-center gap-2">
                        <RepAvatar
                            initials={prospect.owner_initials}
                            color={prospect.owner_color}
                            size="sm"
                        />
                        <span className="text-xs text-desk-muted">
                            {firstNameOf(prospect.owner_name)}
                        </span>
                    </span>
                ),
            },
            {
                key: "engage_on",
                header: "Engage on",
                className: `${CELL_CLASS} w-[160px]`,
                render: (prospect) => <DueBadge prospect={prospect} variant="date" />,
            },
            {
                key: "arr",
                header: "Est. ARR",
                className: `${CELL_CLASS} w-[120px]`,
                render: (prospect) => (
                    <span className="font-bold text-desk-ink">
                        {formatDeskMoney(prospect.estimated_arr)}
                    </span>
                ),
            },
            {
                key: "note",
                header: "Note",
                className: `${CELL_CLASS} w-[300px]`,
                render: (prospect) => (
                    <span className="line-clamp-2 block text-desk-muted">{prospect.note}</span>
                ),
            },
            {
                key: "action",
                header: "",
                className: `${CELL_CLASS} text-right`,
                render: (prospect) => (
                    <Button
                        variant="primary"
                        size="sm"
                        onClick={() => void engage(prospect)}
                        aria-label={`Start engaging ${prospect.company}`}
                    >
                        Start engaging <ArrowRight size={16} />
                    </Button>
                ),
            },
        ],
        [engage]
    );

    return (
        <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <h1 className="text-2xl leading-8 font-bold text-desk-ink">Future Pipeline</h1>
                    <p className="pt-1 text-[13px] text-desk-muted">
                        Planned deals with an engage-by date. Due prospects raise a notification.
                    </p>
                </div>
                <Button variant="primary" size="sm" onClick={() => setIsCreating(true)}>
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

            <div className="overflow-hidden rounded-2xl border border-desk-border bg-desk-surface">
                <div className="flex flex-wrap items-center gap-3 border-b border-desk-border px-4 py-3">
                    <h2 className="text-[13px] font-semibold text-desk-ink">Planned prospects</h2>
                    <span className="rounded-full bg-desk-accent-soft px-2 py-0.5 text-xs font-bold text-priori-purple">
                        {summary?.prospect_count ?? 0}
                    </span>
                    {(summary?.due_count ?? 0) > 0 && (
                        <span className="rounded-full bg-desk-red-soft px-2.5 py-0.5 text-xs font-semibold text-desk-red">
                            {summary?.due_count} due now
                        </span>
                    )}

                    <SearchInput
                        value={search}
                        onSearchChange={setSearch}
                        aria-label="Search prospects"
                        placeholder="Search prospects…"
                        className="ml-auto w-56 bg-desk-bg px-3 py-1.5 [&_input]:text-xs [&_svg]:size-4"
                    />
                </div>

                {isLoading ? (
                    <LoadingState message="Loading prospects..." className="h-64" />
                ) : (
                    <Table
                        columns={columns}
                        data={prospects}
                        rowKey={(prospect) => String(prospect.id)}
                        className={TABLE_OVERRIDES}
                        rowClassName={() => "border-desk-border hover:bg-desk-bg"}
                        emptyMessage="No prospects planned."
                    />
                )}
            </div>

            <AddProspectDialog
                isOpen={isCreating}
                onClose={() => setIsCreating(false)}
                onCreated={() => {
                    setIsCreating(false);
                    setRevision((value) => value + 1);
                }}
            />
        </div>
    );
}
