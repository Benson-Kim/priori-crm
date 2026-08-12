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

import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { LoadingState } from "@/components/ui/LoadingState";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table, type Column } from "@/components/ui/Table";
import { useDebounce } from "@/hooks/useDebounce";
import {
    DuplicateCustomerError,
    engageProspect,
    formatDeskMoney,
    getFuturePipelineSummary,
    getProspects,
    type FuturePipelineSummary,
    type ProspectRow,
} from "@/services/salesDeskApi";
import { AddProspectDialog } from "../components/AddProspectDialog";
import { DueBadge, UrgencyDot } from "../components/DueBadge";

const CELL_CLASS = "align-top";
const TABLE_OVERRIDES =
    "[&_th]:py-3 [&_table]:min-w-[1100px]";

/** First name only, since the column is narrow and the avatar disambiguates. */
const firstNameOf = (name: string) => name.split(" ")[0];

/** An engage that hit the duplicate-customer 409, awaiting a decision. */
interface DuplicatePrompt {
    prospectId: number;
    companyName: string;
    existingCustomerId: number;
}

export default function SalesDeskFuturePipelineWorkspacePage() {
    const navigate = useNavigate();

    const [prospects, setProspects] = useState<ProspectRow[]>([]);
    const [summary, setSummary] = useState<FuturePipelineSummary | null>(null);
    const [search, setSearch] = useState("");
    const debouncedSearch = useDebounce(search, 250);
    const [isCreating, setIsCreating] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);

    const dismissNotice = useCallback(() => setNotice(null), []);

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

    // Set when engaging hits an already-registered company; drives the
    // link-or-cancel dialog below.
    const [duplicate, setDuplicate] = useState<DuplicatePrompt | null>(null);
    const [isLinking, setIsLinking] = useState(false);

    /** Promote the prospect, then follow it through to the deal it became. */
    const engage = useCallback(
        async (prospect: ProspectRow) => {
            try {
                const { dealId } = await engageProspect(prospect.id);
                navigate(`/sales-desk/pipeline/workspace?deal=${dealId}`);
            } catch (err) {
                if (err instanceof DuplicateCustomerError) {
                    setDuplicate({
                        prospectId: prospect.id,
                        companyName: err.companyName,
                        existingCustomerId: err.existingCustomerId,
                    });
                    return;
                }
                setError(err instanceof Error ? err.message : "Could not engage that prospect.");
                setRevision((value) => value + 1);
            }
        },
        [navigate]
    );

    /** Resolve the duplicate by linking the deal to the existing customer. */
    const linkToExisting = useCallback(async () => {
        if (!duplicate) return;
        setIsLinking(true);
        try {
            const { dealId } = await engageProspect(
                duplicate.prospectId,
                duplicate.existingCustomerId
            );
            navigate(`/sales-desk/pipeline/workspace?deal=${dealId}`);
        } catch (err) {
            setDuplicate(null);
            setError(err instanceof Error ? err.message : "Could not engage that prospect.");
            setRevision((value) => value + 1);
        } finally {
            setIsLinking(false);
        }
    }, [duplicate, navigate]);

    const columns = useMemo<Column<ProspectRow>[]>(
        () => [
            {
                key: "company",
                header: "Company",
                className: `${CELL_CLASS} w-[220px]`,
                render: (prospect) => (
                    <span className="flex items-center gap-2">
                        <UrgencyDot urgency={prospect.urgency} />
                        <span className="font-semibold text-sd-ink">{prospect.company}</span>
                    </span>
                ),
            },
            {
                key: "contact",
                header: "Contact",
                className: `${CELL_CLASS} w-[180px]`,
                render: (prospect) => (
                    <span className="text-sd-muted">{prospect.contact || "—"}</span>
                ),
            },
            {
                key: "owner",
                header: "Owner",
                className: `${CELL_CLASS} w-[140px]`,
                render: (prospect) => (
                    <span className="flex items-center gap-2">
                        <Avatar
                            name={prospect.owner_name}
                            initials={prospect.owner_initials}
                            color={prospect.owner_color}
                            size={24}
                        />
                        <span className="text-xs text-sd-muted">
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
                    <span className="font-bold text-sd-ink">
                        {formatDeskMoney(prospect.estimated_arr)}
                    </span>
                ),
            },
            {
                key: "note",
                header: "Note",
                className: `${CELL_CLASS} w-[300px]`,
                render: (prospect) => (
                    <span className="line-clamp-2 block text-sd-muted">{prospect.note}</span>
                ),
            },
            {
                key: "action",
                header: "",
                className: `${CELL_CLASS} text-right`,
                render: (prospect) => (
                    <Button
                        variant="primary"
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

            {/* Heading and search share the line; the card starts at the table. */}
            <div className="flex flex-wrap items-center gap-3 sm:flex-nowrap">
                <h2 className="text-[13px] font-semibold text-sd-ink">Planned prospects</h2>
                <span className="rounded-full bg-sd-brand-bg px-2 py-0.5 text-xs font-bold text-priori-purple">
                    {summary?.prospect_count ?? 0}
                </span>
                {(summary?.due_count ?? 0) > 0 && (
                    <span className="rounded-full bg-sd-danger-bg px-2.5 py-0.5 text-xs font-semibold text-sd-danger">
                        {summary?.due_count} due now
                    </span>
                )}

                <SearchInput
                    value={search}
                    onSearchChange={setSearch}
                    aria-label="Search prospects"
                    placeholder="Search prospects…"
                    className="ml-auto h-control w-56 shrink-0 bg-sd-card px-3 [&_svg]:size-4"
                />
            </div>

            <div className="overflow-hidden rounded-2xl border border-sd-border bg-sd-card shadow-sd-card">
                {isLoading ? (
                    <LoadingState message="Loading prospects..." className="h-64" />
                ) : (
                    <Table
                        columns={columns}
                        data={prospects}
                        rowKey={(prospect) => String(prospect.id)}
                        variant="sales-desk"
                        className={TABLE_OVERRIDES}
                        emptyMessage="No prospects planned."
                    />
                )}
            </div>

            <AddProspectDialog
                isOpen={isCreating}
                onClose={() => setIsCreating(false)}
                onCreated={() => {
                    setIsCreating(false);
                    setNotice("Planned deal added — you'll be notified when it's due");
                    setRevision((value) => value + 1);
                }}
            />

            {/* The store already knows this company; link rather than duplicate. */}
            <Dialog
                isOpen={duplicate !== null}
                onClose={() => setDuplicate(null)}
                title="Customer already exists"
                description={
                    duplicate
                        ? `${duplicate.companyName} is already a registered customer. Link this deal to the existing customer record instead of registering a new one?`
                        : undefined
                }
                confirmLabel="Link to existing customer"
                cancelLabel="Cancel"
                onConfirm={() => void linkToExisting()}
                isLoading={isLinking}
            />
        </div>
    );
}
