import { cn } from "@/lib/utils";

import { Chip, type ChipTone } from "./Chip";

/**
 * How long a deal has been in play, and how long since anyone touched it,
 * both tone-coded so a rep can spot a deal that has gone quiet without doing
 * arithmetic on dates.
 *
 * Age and silence run on different scales, and only silence escalates. A deal
 * can sit in the pipeline for months and still be worth working, so age reads
 * fresh or ageing and never alarms; a month of no contact is a problem, so the
 * activity line turns red. Closed deals show a neutral total, since age stops
 * being actionable once the deal has resolved.
 *
 * Both numbers are server-computed. Ages are never derived client-side from
 * dates, so every screen agrees on the same day boundary.
 */

/** Days in pipeline before a deal reads as ageing rather than fresh. */
const AGE_FRESH_DAYS = 7;

/** Days of silence before the activity line escalates to red. */
const IDLE_STALE_DAYS = 30;

const ageTone = (days: number): ChipTone => (days <= AGE_FRESH_DAYS ? "fresh" : "aging");

interface AgeRibbonProps {
    /** Server-computed days in pipeline. */
    ageDays: number;
    /** Server-computed days since last activity. Omitted on closed deals. */
    idleDays?: number;
    /** Closed deals render the neutral total instead. */
    closed?: boolean;
    className?: string;
}

export function AgeRibbon({
    ageDays,
    idleDays,
    closed = false,
    className,
}: Readonly<AgeRibbonProps>) {
    if (closed) {
        return (
            <Chip tone="neutral" className={className}>
                {ageDays}d total
            </Chip>
        );
    }

    return (
        <div className={cn("flex flex-col items-start gap-1.5", className)}>
            <Chip tone={ageTone(ageDays)}>{ageDays}d in pipeline</Chip>
            {idleDays !== undefined && (
                <p
                    className={cn(
                        "text-dense-sm",
                        idleDays >= IDLE_STALE_DAYS
                            ? "font-medium text-sd-danger"
                            : "text-sd-muted"
                    )}
                >
                    Last activity {idleDays}d ago
                </p>
            )}
        </div>
    );
}
