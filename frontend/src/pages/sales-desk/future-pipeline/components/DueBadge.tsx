/**
 * DueBadge
 *
 * How close a prospect's engage-by date is, in two readings. The cards want
 * urgency at a glance so they show the countdown; the table already has an
 * "Engage on" column, so there it shows the date and only escalates to a word
 * once the date has arrived. Colour never carries the meaning alone.
 */

import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/utils";
import type { ProspectRow } from "@/services/salesDeskApi";

const URGENCY_CLASSES: Record<ProspectRow["urgency"], string> = {
    overdue: "bg-desk-red-soft text-desk-red",
    today: "bg-desk-warm-soft text-desk-amber",
    scheduled: "bg-desk-neutral-soft text-desk-muted",
};

interface DueBadgeProps {
    prospect: Pick<ProspectRow, "urgency" | "days_until" | "engage_on">;
    /** "countdown" for the cards, "date" for the table. */
    variant?: "countdown" | "date";
    className?: string;
}

function labelFor(
    prospect: DueBadgeProps["prospect"],
    variant: NonNullable<DueBadgeProps["variant"]>
): string {
    if (prospect.urgency === "overdue") {
        return variant === "countdown"
            ? "Overdue"
            : `Overdue · ${formatDate(prospect.engage_on)}`;
    }
    if (prospect.urgency === "today") return "Today";
    return variant === "countdown"
        ? `${prospect.days_until}d`
        : formatDate(prospect.engage_on);
}

export function DueBadge({
    prospect,
    variant = "countdown",
    className,
}: Readonly<DueBadgeProps>) {
    return (
        <span
            className={cn(
                "inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
                URGENCY_CLASSES[prospect.urgency],
                className
            )}
        >
            {labelFor(prospect, variant)}
        </span>
    );
}

/** Standalone urgency dot for the table's company column. */
export function UrgencyDot({ urgency }: Readonly<{ urgency: ProspectRow["urgency"] }>) {
    return (
        <span
            className={cn(
                "inline-block size-2 shrink-0 rounded-full",
                urgency === "overdue"
                    ? "bg-desk-red"
                    : urgency === "today"
                      ? "bg-desk-amber"
                      : "bg-desk-green"
            )}
            aria-hidden="true"
        />
    );
}
