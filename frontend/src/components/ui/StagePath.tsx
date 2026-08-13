import { cn } from "@/lib/utils";
import { STAGE_STEPS, type DealStatus } from "@/lib/sales-desk-stages";

/**
 * A deal's progress as discrete segments, because the stages are named steps
 * rather than a percentage. Cleared stages are pale, the current one is solid,
 * and the closing segment turns green or red once the deal resolves.
 *
 * Both variants take the stage index the server computed rather than looking a
 * stage name up in a list, so the client never has an opinion about where a
 * deal sits.
 */

interface StageProgress {
    /** Server-computed index into the stage list. */
    stageIndex: number;
    status: DealStatus;
    /** Server-rendered stage or outcome name, used for the caption. */
    stageLabel: string;
    /** Why the deal closed. Present only on won/lost deals. */
    closeReason?: string | null;
}

function segmentClass(index: number, status: DealStatus, stageIndex: number): string {
    const isClosingSegment = index === STAGE_STEPS - 1;

    if (status !== "open") {
        if (isClosingSegment) return status === "won" ? "bg-sd-won" : "bg-sd-lost";
        /*
         * Only the stages the deal actually reached are filled. A deal that
         * died at Proposal & Quote must not show Negotiation as cleared, or
         * the bar misreports the path it took.
         */
        return index <= stageIndex ? "bg-sd-progress-done" : "bg-sd-border";
    }

    if (index < stageIndex) return "bg-sd-progress-done";
    if (index === stageIndex) return "bg-sd-progress-current";
    return "bg-sd-border";
}

function captionOf({ status, stageIndex, stageLabel, closeReason }: StageProgress): string {
    if (status === "open") {
        return `Stage ${stageIndex + 1} of ${STAGE_STEPS} · ${stageLabel}`;
    }
    return closeReason ? `${stageLabel}, ${closeReason}` : stageLabel;
}

interface StagePathProps extends StageProgress {
    /** `list` is the 10px deal-row bar; `mini` the 6px drawer/card bar. */
    variant?: "list" | "mini";
    /** Hide the caption line, e.g. on the company drawer's mini deal card. */
    showCaption?: boolean;
    className?: string;
}

export function StagePath({
    variant = "list",
    showCaption = true,
    className,
    ...progress
}: Readonly<StagePathProps>) {
    const caption = captionOf(progress);

    return (
        <div className={cn("flex flex-col gap-1.5", className)}>
            <div
                role="img"
                aria-label={caption}
                className={cn("flex w-full gap-px", variant === "list" ? "h-2.5" : "h-1.5")}
            >
                {Array.from({ length: STAGE_STEPS }, (_, index) => (
                    <span
                        key={index}
                        className={cn(
                            "flex-1 first:rounded-l last:rounded-r",
                            segmentClass(index, progress.status, progress.stageIndex)
                        )}
                    />
                ))}
            </div>
            {showCaption && (
                <p
                    className={cn(
                        "text-[11px] font-medium",
                        progress.status === "open" && "text-sd-muted",
                        progress.status === "won" && "text-sd-success",
                        progress.status === "lost" && "text-sd-danger"
                    )}
                >
                    {caption}
                </p>
            )}
        </div>
    );
}
