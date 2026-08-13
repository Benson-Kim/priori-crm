/**
 * DealDrawer
 *
 * The deal's working surface. Stage tabs replay what was recorded at each
 * step; the actions underneath move the deal on. Every action requires a note
 * first, which becomes the stage record the next person reads. Closing also
 * needs a reason from the shared vocabulary, so win/loss analysis stays
 * countable rather than free text.
 */

import { ArrowRight } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { cn, formatDate, plural } from "@/lib/utils";
import {
    DEAL_STAGES,
    LOST_REASONS,
    WON_REASONS,
    formatDeskMoneyCompact,
    taxTreatmentLabel,
    type DealDetail,
    type DealRecord,
} from "@/services/salesDeskApi";
import { Drawer, DrawerSection } from "@/components/ui/Drawer";
import { Avatar } from "@/components/ui/Avatar";
import { SyncPill } from "@/components/ui/SyncPill";

/** Which closing form, if any, is currently expanded. */
type ClosingOutcome = "won" | "lost" | null;

/** Shortened tab labels, so all five steps fit one row inside the panel. */
const STAGE_TAB_LABELS: Record<string, string> = {
    "Proposal & Quote": "Prop. & Quote",
    "Closed · Won": "Won",
    "Closed · Lost": "Lost",
};

interface DealDrawerProps {
    detail: DealDetail;
    onClose: () => void;
    onLogActivity: (note: string) => Promise<void>;
    onAdvance: (note: string) => Promise<void>;
    onCloseDeal: (outcome: "won" | "lost", reason: string, note: string) => Promise<void>;
    onMoveToFuture: (note: string) => Promise<void>;
}

function StatTile({ value, label }: Readonly<{ value: string; label: string }>) {
    return (
        <div className="rounded-xl border border-sd-border px-3 py-2">
            <p className="text-[13px] font-bold text-sd-ink">{value}</p>
            <p className="pt-0.5 text-[11px] leading-tight text-sd-muted">{label}</p>
        </div>
    );
}

export function DealDrawer({
    detail,
    onClose,
    onLogActivity,
    onAdvance,
    onCloseDeal,
    onMoveToFuture,
}: Readonly<DealDrawerProps>) {
    const { deal, billing_profile: profile, billed_value: billedValue, next_stage: nextStage } =
        detail;

    const [note, setNote] = useState("");
    const [closing, setClosing] = useState<ClosingOutcome>(null);
    const [reason, setReason] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);

    /*
     * The open tab is derived, not stored: until the user picks one it tracks
     * the newest record, so advancing a deal moves the panel onto the stage
     * just written.
     */
    const [pickedStage, setPickedStage] = useState<string | null>(null);
    const activeStage = pickedStage ?? deal.latest_record.stage;

    /*
     * The whole path is shown, not just the stages reached, with anything not
     * yet reached left muted and unclickable.
     */
    const history = deal.history;
    const stageTabs = useMemo(() => {
        const recordsByStage = new Map<string, DealRecord>();
        history.forEach((record) => recordsByStage.set(record.stage, record));

        const closingStage = deal.status === "lost" ? "Closed · Lost" : "Closed · Won";
        const steps = [...DEAL_STAGES, closingStage];

        return steps.map((stage) => ({
            stage,
            label: STAGE_TAB_LABELS[stage] ?? stage,
            record: recordsByStage.get(stage) ?? null,
        }));
    }, [history, deal.status]);

    const shownRecord =
        stageTabs.find((tab) => tab.stage === activeStage)?.record ?? deal.latest_record;

    /** Run an action behind the shared note requirement and error handling. */
    const submit = async (action: () => Promise<void>) => {
        if (!note.trim()) {
            setError("Add a note first. It becomes the stage record.");
            return;
        }
        setIsSaving(true);
        setError(null);
        try {
            await action();
            setNote("");
            setClosing(null);
            setReason("");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save that.");
        } finally {
            setIsSaving(false);
        }
    };

    const confirmClose = () => {
        if (!closing) return;
        if (!reason) {
            setError("Pick a reason so win/loss analysis stays countable.");
            return;
        }
        void submit(() => onCloseDeal(closing, reason, note));
    };

    const reasons = closing === "won" ? WON_REASONS : LOST_REASONS;

    return (
        <Drawer
            label={`${deal.company_name} deal`}
            onClose={onClose}
            header={
                <div className="flex items-start gap-3">
                    <Avatar
                        name={deal.owner_name}
                        initials={deal.owner_initials}
                        color={deal.owner_color}
                        size={36}
                    />
                    <div className="min-w-0">
                        <h2 className="text-[13px] font-semibold text-sd-ink">
                            {deal.company_name}
                        </h2>
                        <p className="pt-0.5 text-[11px] leading-relaxed text-sd-muted">
                            {deal.product} · {plural(deal.seats, "seat")}
                        </p>
                        <p className="text-[11px] text-sd-muted">Owned by {deal.owner_name}</p>
                    </div>
                </div>
            }
        >
            <DrawerSection>
                <div className="grid grid-cols-3 gap-2">
                    <StatTile
                        value={formatDeskMoneyCompact(billedValue, deal.billing_currency)}
                        label={`Annual value · ${deal.billing_currency}`}
                    />
                    <StatTile value={`${deal.age_days}d`} label="In pipeline" />
                    <StatTile value={`${deal.idle_days}d`} label="Since last activity" />
                </div>

                {/* Which profile this will invoice against */}
                <p className="flex flex-wrap items-center gap-x-1 text-[11px] text-sd-muted">
                    Billing to{" "}
                    <span className="font-mono font-semibold text-sd-ink">{profile.code}</span> ·{" "}
                    {profile.terms} · {taxTreatmentLabel(profile.tax)} ·
                    <SyncPill synced={profile.synced} className="text-[11px]" />
                </p>
            </DrawerSection>

            <DrawerSection label="Stage record">
                <div className="flex flex-wrap gap-1">
                    {stageTabs.map(({ stage, label, record }) => {
                        const isReached = record !== null;
                        return (
                            <button
                                key={stage}
                                type="button"
                                disabled={!isReached}
                                title={isReached ? undefined : "Not reached yet"}
                                onClick={() => setPickedStage(stage)}
                                className={cn(
                                    "rounded-lg px-1.5 py-1 text-[11px] font-medium whitespace-nowrap transition-colors",
                                    stage === activeStage && isReached
                                        ? "bg-sd-ink text-white"
                                        : "bg-sd-neutral-bg text-sd-muted",
                                    isReached
                                        ? "hover:text-sd-ink"
                                        : "cursor-not-allowed opacity-60"
                                )}
                            >
                                {label}
                            </button>
                        );
                    })}
                </div>

                <div>
                    <div className="flex items-center gap-2">
                        <span
                            className="size-1.5 shrink-0 rounded-full bg-priori-purple"
                            aria-hidden="true"
                        />
                        <span className="text-xs font-semibold text-sd-ink">
                            {shownRecord.stage}
                        </span>
                        <span className="text-[11px] text-sd-muted">
                            {formatDate(shownRecord.logged_on, "table")}
                        </span>
                    </div>
                    <p className="pt-1 text-xs leading-relaxed text-sd-ink">{shownRecord.note}</p>
                </div>
            </DrawerSection>

            {/* A closed deal is read-only, so the action block is omitted. */}
            {deal.status === "open" && (
                <DrawerSection className="gap-3">
                    <label
                        htmlFor="deal-note"
                        className="text-[10px] font-bold tracking-[1px] text-sd-muted uppercase"
                    >
                        Log or move
                    </label>
                        <textarea
                            id="deal-note"
                            rows={3}
                            value={note}
                            onChange={(event) => setNote(event.target.value)}
                            aria-invalid={!!error}
                            aria-describedby={error ? "deal-note-error" : undefined}
                            placeholder={`What happened at ${deal.stage_label}? A note is required to advance or close.`}
                            className="w-full resize-y rounded-xl border border-sd-border px-3 py-2 text-xs text-sd-ink placeholder:text-sd-muted"
                        />

                        {error && (
                            <p
                                id="deal-note-error"
                                role="alert"
                                className="text-[11px] text-sd-danger"
                            >
                                {error}
                            </p>
                        )}

                        {closing === null ? (
                            <>
                                <div className="flex flex-wrap items-center gap-2">
                                    <Button
                                        variant="outline-secondary"
                                        className="h-control"
                                        loading={isSaving}
                                        onClick={() => void submit(() => onLogActivity(note))}
                                    >
                                        Log activity
                                    </Button>
                                    {nextStage && (
                                        <Button
                                            variant="primary"
                                            loading={isSaving}
                                            onClick={() => void submit(() => onAdvance(note))}
                                        >
                                            Advance to {nextStage} <ArrowRight size={16} />
                                        </Button>
                                    )}
                                    <Button
                                        variant="outline-success"
                                        className="h-control"
                                        disabled={isSaving}
                                        onClick={() => {
                                            setClosing("won");
                                            setError(null);
                                        }}
                                    >
                                        Close · Won
                                    </Button>
                                </div>

                                <Button
                                    variant="outline-danger"
                                    disabled={isSaving}
                                    className="h-control w-full"
                                    onClick={() => {
                                        setClosing("lost");
                                        setError(null);
                                    }}
                                >
                                    Close · Lost
                                </Button>

                                <Button
                                    variant="link"
                                    loading={isSaving}
                                    className="self-start px-0"
                                    onClick={() => void submit(() => onMoveToFuture(note))}
                                >
                                    Move to future pipeline <ArrowRight size={16} />
                                </Button>
                            </>
                        ) : (
                            <div className="flex flex-col gap-2">
                                <InlineSelect
                                    variant="sales-desk"
                                    id="close-reason"
                                    label={`Why was this deal ${closing}?`}
                                    placeholder="Select a reason…"
                                    value={reason}
                                    onChange={setReason}
                                    options={reasons.map((option) => ({
                                        value: option,
                                        label: option,
                                    }))}
                                />
                                <div className="flex items-center gap-2">
                                    <Button
                                        variant={closing === "won" ? "success" : "danger"}
                                        loading={isSaving}
                                        onClick={confirmClose}
                                    >
                                        Confirm · {closing === "won" ? "Won" : "Lost"}
                                    </Button>
                                    <Button
                                        variant="outline-secondary"
                                        className="h-control"
                                        disabled={isSaving}
                                        onClick={() => {
                                            setClosing(null);
                                            setReason("");
                                            setError(null);
                                        }}
                                    >
                                        Cancel
                                    </Button>
                                </div>
                            </div>
                        )}
                </DrawerSection>
            )}
        </Drawer>
    );
}
