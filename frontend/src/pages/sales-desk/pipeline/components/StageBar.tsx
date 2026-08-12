/**
 * StageBar
 *
 * A deal's progress as five discrete segments, because the stages are named
 * steps rather than a percentage. Cleared stages are pale, the current one is
 * solid, and the closing segment turns green or red once the deal resolves.
 */

import { cn } from "@/lib/utils";
import { DEAL_STAGES, type PipelineDeal } from "@/services/salesDeskApi";

/** Four selling stages plus the closing step. */
const TOTAL_STEPS = DEAL_STAGES.length + 1;

interface StageBarProps {
    deal: Pick<PipelineDeal, "stage_index" | "stage_label" | "status" | "close_reason">;
}

function segmentClass(index: number, deal: StageBarProps["deal"]): string {
    const isClosingSegment = index === TOTAL_STEPS - 1;

    if (deal.status !== "open") {
        if (isClosingSegment) {
            return deal.status === "won" ? "bg-desk-stage-won" : "bg-desk-stage-lost";
        }
        return "bg-desk-stage-done";
    }

    if (index < deal.stage_index) return "bg-desk-stage-done";
    if (index === deal.stage_index) return "bg-desk-stage-current";
    return "bg-desk-border";
}

export function StageBar({ deal }: Readonly<StageBarProps>) {
    const caption =
        deal.status === "open"
            ? `Stage ${deal.stage_index + 1} of ${TOTAL_STEPS} · ${deal.stage_label}`
            : `${deal.stage_label}${deal.close_reason ? ` — ${deal.close_reason}` : ""}`;

    return (
        <div>
            <div className="flex w-full gap-px" role="img" aria-label={caption}>
                {Array.from({ length: TOTAL_STEPS }, (_, index) => (
                    <span
                        key={index}
                        className={cn(
                            "h-2.5 flex-1",
                            index === 0 && "rounded-l",
                            index === TOTAL_STEPS - 1 && "rounded-r",
                            segmentClass(index, deal)
                        )}
                    />
                ))}
            </div>
            <p
                className={cn(
                    "pt-1.5 text-[11px] font-medium",
                    deal.status === "open" && "text-desk-muted",
                    deal.status === "won" && "text-desk-green",
                    deal.status === "lost" && "text-desk-red"
                )}
            >
                {caption}
            </p>
        </div>
    );
}
