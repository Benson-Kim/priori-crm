/**
 * StagePill
 *
 * A deal's stage, or its outcome once closed. The ramp runs cool to warm as a
 * deal progresses, so a column of pills reads as a heat map of the book.
 */

import { cn } from "@/lib/utils";
import { DEAL_STAGES, type PipelineDeal } from "@/services/salesDeskApi";

const STAGE_CLASSES: Record<string, string> = {
    Activation: "bg-desk-blue-soft text-desk-blue",
    Qualification: "bg-desk-amber-soft text-desk-amber",
    "Proposal & Quote": "bg-desk-accent-soft text-priori-purple",
    Negotiation: "bg-desk-green-soft text-desk-green",
    Won: "bg-desk-green-soft text-desk-green",
    Lost: "bg-desk-red-soft text-desk-red",
};

interface StagePillProps {
    deal: Pick<PipelineDeal, "stage_label">;
    className?: string;
}

export function StagePill({ deal, className }: Readonly<StagePillProps>) {
    return (
        <span
            className={cn(
                "inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
                STAGE_CLASSES[deal.stage_label] ?? "bg-desk-neutral-soft text-desk-muted",
                className
            )}
        >
            {deal.stage_label}
        </span>
    );
}

/** Total number of steps a deal walks, including the closing step. */
export const TOTAL_STAGE_STEPS = DEAL_STAGES.length + 1;
