import { cn } from "@/lib/utils";

import type { DealStage } from "./Chip";

/**
 * StagePath / StageStepper — Sales Desk deal-stage visualisation.
 *
 * Verbatim to `docs/sales-desk-designs/style-reference.md` §3 and
 * `Define_design_guidelines.svg`:
 * - `StagePath`: 5 segments (10px list / 6px mini/drawer) — completed
 *   `#93C5FD`, current `#1D4ED8`, upcoming `#E4E8EF`; closed deals render
 *   4 completed segments + a green `#16A34A` (won) or red `#EF4444` (lost)
 *   end-cap. Caption: `Stage N of 5 · <Stage>` / `Won — {reason}` /
 *   `Lost — {reason}`.
 * - `StageStepper`: drawer variant — labelled pills, current `#16233A`
 *   filled white text, upcoming `#F3F4F6`/`#9CA3AF`; "Proposal & Quote"
 *   abbreviates to `Prop. & Quote`.
 */

/** The 5 pipeline stages in fixed order. */
export const PIPELINE_STAGES = [
  "Activation",
  "Qualification",
  "Proposal & Quote",
  "Negotiation",
  "Won",
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export type DealStatus = "open" | "won" | "lost";

const SEGMENT_COUNT = PIPELINE_STAGES.length;

interface StagePathProps {
  /** Current stage of an open deal (ignored when status is won/lost). */
  stage?: DealStage;
  status?: DealStatus;
  /** Close reason for the `Won — {reason}` / `Lost — {reason}` caption. */
  reason?: string;
  /** `list` = 10px segments (deal list); `mini` = 6px (drawer/company card). */
  variant?: "list" | "mini";
  /** Hide the caption line (e.g. the company-drawer mini deal card). */
  showCaption?: boolean;
  className?: string;
}

function segmentColor(
  index: number,
  currentIndex: number,
  status: DealStatus
): string {
  const isEndCap = index === SEGMENT_COUNT - 1;
  if (status === "won") return isEndCap ? "bg-sd-won" : "bg-sd-progress-done";
  if (status === "lost") return isEndCap ? "bg-sd-lost" : "bg-sd-progress-done";
  if (index < currentIndex) return "bg-sd-progress-done";
  if (index === currentIndex) return "bg-sd-progress-current";
  return "bg-sd-border";
}

export function StagePath({
  stage = "Activation",
  status = "open",
  reason,
  variant = "list",
  showCaption = true,
  className,
}: Readonly<StagePathProps>) {
  const currentIndex = Math.max(
    0,
    PIPELINE_STAGES.indexOf(stage as PipelineStage)
  );

  const caption =
    status === "won"
      ? `Won${reason ? ` — ${reason}` : ""}`
      : status === "lost"
        ? `Lost${reason ? ` — ${reason}` : ""}`
        : `Stage ${currentIndex + 1} of ${SEGMENT_COUNT} · ${PIPELINE_STAGES[currentIndex]}`;

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div
        role="img"
        aria-label={caption}
        className={cn("flex gap-0.5", variant === "list" ? "h-2.5" : "h-1.5")}
      >
        {PIPELINE_STAGES.map((name, index) => (
          <span
            key={name}
            className={cn(
              "flex-1 first:rounded-l-full last:rounded-r-full",
              segmentColor(index, currentIndex, status)
            )}
          />
        ))}
      </div>
      {showCaption && (
        <p
          className={cn(
            "text-[11px] font-medium",
            status === "won"
              ? "text-sd-won"
              : status === "lost"
                ? "text-sd-danger"
                : "text-sd-muted"
          )}
        >
          {caption}
        </p>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- */
/* Drawer stepper — labelled stage pills inside the 390px deal drawer */
/* ----------------------------------------------------------------- */

/** Drawer pill labels; "Proposal & Quote" abbreviates per the design. */
const STEPPER_LABELS: Record<PipelineStage, string> = {
  Activation: "Activation",
  Qualification: "Qualification",
  "Proposal & Quote": "Prop. & Quote",
  Negotiation: "Negotiation",
  Won: "Won",
};

interface StageStepperProps {
  stage?: DealStage;
  status?: DealStatus;
  className?: string;
}

export function StageStepper({
  stage = "Activation",
  status = "open",
  className,
}: Readonly<StageStepperProps>) {
  const currentIndex = Math.max(
    0,
    PIPELINE_STAGES.indexOf(stage as PipelineStage)
  );

  return (
    <div className={cn("flex gap-0.5", className)}>
      {PIPELINE_STAGES.map((name, index) => {
        const isEndCap = index === SEGMENT_COUNT - 1;
        // Closed deals mirror StagePath: completed run + coloured end-cap.
        // (The design only shows the stage-1 state; completed segments reuse
        // the path's `#93C5FD` completed colour.)
        const pillClass =
          status === "won" && isEndCap
            ? "bg-sd-won text-white"
            : status === "lost" && isEndCap
              ? "bg-sd-lost text-white"
              : status !== "open" || index < currentIndex
                ? "bg-sd-progress-done text-white"
                : index === currentIndex
                  ? "bg-sd-ink text-white"
                  : "bg-[#F3F4F6] text-sd-faint";

        return (
          <span
            key={name}
            aria-current={
              status === "open" && index === currentIndex ? "step" : undefined
            }
            className={cn(
              "flex-1 truncate rounded px-1.5 py-1.5 text-center text-[10px] font-semibold",
              pillClass
            )}
          >
            {STEPPER_LABELS[name]}
          </span>
        );
      })}
    </div>
  );
}
