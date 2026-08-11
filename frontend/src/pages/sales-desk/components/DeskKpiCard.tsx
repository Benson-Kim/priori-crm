/**
 * DeskKpiCard
 *
 * A headline metric. Distinct from the shared `MetricCard`, which pairs a
 * value with a delta badge: the desk's KPIs carry a qualifying subtitle and
 * colour the value itself to signal what kind of number it is.
 */

import { cn } from "@/lib/utils";

/** Semantic colour of the headline figure. */
export type DeskKpiTone = "neutral" | "forecast" | "won" | "lost";

const TONE_CLASSES: Record<DeskKpiTone, string> = {
    neutral: "text-desk-ink",
    forecast: "text-desk-blue",
    won: "text-desk-green",
    lost: "text-desk-red",
};

interface DeskKpiCardProps {
    label: string;
    value: string;
    /** Qualifier under the figure, such as a deal count. */
    caption: string;
    tone?: DeskKpiTone;
}

export function DeskKpiCard({
    label,
    value,
    caption,
    tone = "neutral",
}: Readonly<DeskKpiCardProps>) {
    return (
        <div className="rounded-2xl border border-desk-border bg-desk-surface p-4 shadow-[0_1px_1.5px_rgba(0,0,0,0.06)]">
            <p className="text-xs font-semibold tracking-[0.3px] text-desk-muted uppercase">
                {label}
            </p>
            <p className={cn("pt-1 text-[25.6px] leading-[33.28px] font-bold", TONE_CLASSES[tone])}>
                {value}
            </p>
            <p className="pt-1 text-xs text-desk-muted">{caption}</p>
        </div>
    );
}
