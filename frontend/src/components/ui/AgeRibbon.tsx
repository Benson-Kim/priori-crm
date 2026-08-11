import { cn } from "@/lib/utils";

import { Chip } from "./Chip";

/**
 * AgeRibbon — Sales Desk deal age / idleness indicator.
 *
 * Verbatim to `docs/sales-desk-designs/style-reference.md` §3/§5:
 * chip `Nd in pipeline` (green fresh ≤7d, amber aging >7d, gray
 * `Nd total` when closed) + line "Last activity Nd ago" (red `#C23434`
 * when stale, i.e. 30d+).
 *
 * IMPORTANT: driven ONLY by the server-computed `age_days` / `idle_days`
 * values — never compute ages client-side from dates.
 */

/** "No activity 30d+" hygiene bucket — idle line turns red from here. */
const STALE_IDLE_DAYS = 30;
/** Fresh (green) chip threshold — aging (amber) beyond this. */
const FRESH_AGE_DAYS = 7;

interface AgeRibbonProps {
  /** Server-computed days in pipeline (`age_days`). */
  ageDays: number;
  /** Server-computed days since last activity (`idle_days`). */
  idleDays?: number;
  /** Closed deals render the gray `Nd total` chip. */
  closed?: boolean;
  className?: string;
}

export function AgeRibbon({
  ageDays,
  idleDays,
  closed = false,
  className,
}: Readonly<AgeRibbonProps>) {
  const tone = closed ? "neutral" : ageDays <= FRESH_AGE_DAYS ? "fresh" : "aging";
  const label = closed ? `${ageDays}d total` : `${ageDays}d in pipeline`;
  const stale = idleDays !== undefined && idleDays >= STALE_IDLE_DAYS;

  return (
    <div className={cn("flex flex-col items-start gap-1", className)}>
      <Chip tone={tone}>{label}</Chip>
      {idleDays !== undefined && (
        <p
          className={cn(
            "text-[11px]",
            stale ? "font-medium text-sd-danger" : "text-sd-muted"
          )}
        >
          Last activity {idleDays}d ago
        </p>
      )}
    </div>
  );
}
