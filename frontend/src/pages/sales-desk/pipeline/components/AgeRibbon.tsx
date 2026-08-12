/**
 * AgeRibbon
 *
 * How long a deal has been in the pipeline and how long since anyone touched
 * it, both tone-coded so a rep can spot a deal that has gone quiet without
 * doing arithmetic on dates. Closed deals show a neutral total, since age
 * stops being actionable once the deal has resolved.
 */

import { cn } from "@/lib/utils";
import { STALE_AFTER_DAYS, type PipelineDeal } from "@/services/salesDeskApi";

type AgeTone = "fresh" | "warm" | "overdue" | "neutral";

const TONE_CLASSES: Record<AgeTone, string> = {
    fresh: "bg-desk-fresh-soft text-desk-fresh",
    warm: "bg-desk-warm-soft text-desk-warm",
    overdue: "bg-desk-red-soft text-desk-red",
    neutral: "bg-desk-neutral-soft text-desk-muted",
};

/** Days-in-pipeline banding. */
const ageTone = (days: number): AgeTone =>
    days <= 14 ? "fresh" : days <= 45 ? "warm" : "overdue";

/** Days-since-activity banding, tighter because silence bites sooner. */
const idleTone = (days: number): AgeTone =>
    days <= 7 ? "fresh" : days <= STALE_AFTER_DAYS ? "warm" : "overdue";

interface AgeRibbonProps {
    deal: Pick<PipelineDeal, "age_days" | "idle_days" | "status">;
}

export function AgeRibbon({ deal }: Readonly<AgeRibbonProps>) {
    if (deal.status !== "open") {
        return (
            <span
                className={cn(
                    "inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold",
                    TONE_CLASSES.neutral
                )}
            >
                {deal.age_days}d total
            </span>
        );
    }

    const idle = idleTone(deal.idle_days);

    return (
        <div>
            <span
                className={cn(
                    "inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold",
                    TONE_CLASSES[ageTone(deal.age_days)]
                )}
            >
                {deal.age_days}d in pipeline
            </span>
            <p
                className={cn(
                    "pt-1.5 text-[11px]",
                    idle === "overdue" ? "text-desk-red" : "text-desk-muted"
                )}
            >
                Last activity {deal.idle_days}d ago
            </p>
        </div>
    );
}
