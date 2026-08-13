import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Fully-rounded pills: stage, status and currency badges across the Sales
 * Desk. One tone scale backs all of them, so a chip's colour means the same
 * thing wherever it appears.
 *
 * Chips that need to read a domain record (a prospect's urgency, say) stay in
 * their module and compose `Chip` rather than being added here.
 */

export type ChipTone =
    | "brand"
    | "info"
    | "success"
    | "warn"
    | "danger"
    | "neutral"
    | "fresh"
    | "aging";

const TONE_STYLES: Record<ChipTone, string> = {
    brand: "bg-sd-brand-bg text-sd-brand",
    info: "bg-sd-info-bg text-sd-info",
    success: "bg-sd-success-bg text-sd-success",
    warn: "bg-sd-warn-bg text-sd-warn",
    danger: "bg-sd-danger-bg text-sd-danger",
    neutral: "bg-sd-neutral-bg text-sd-muted",
    fresh: "bg-sd-fresh-bg text-sd-fresh",
    aging: "bg-sd-aging-bg text-sd-aging",
};

interface ChipProps {
    tone: ChipTone;
    children: ReactNode;
    className?: string;
}

export function Chip({ tone, children, className }: Readonly<ChipProps>) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
                TONE_STYLES[tone],
                className
            )}
        >
            {children}
        </span>
    );
}

/* ---------------------------------------------------------------- */
/* Status: Synced means accounting holds the record; Draft and       */
/* Pending are both "not there yet", which is why neither is green.  */
/* ---------------------------------------------------------------- */

export type ChipStatus = "Draft" | "Pending" | "Synced";

const STATUS_TONES: Record<ChipStatus, ChipTone> = {
    Draft: "neutral",
    Pending: "warn",
    Synced: "success",
};

export function StatusChip({
    status,
    className,
}: Readonly<{ status: ChipStatus; className?: string }>) {
    return (
        <Chip tone={STATUS_TONES[status]} className={className}>
            {status}
        </Chip>
    );
}

/* ------------------------------------------------------------------ */
/* Stage: the ramp runs cool to warm as a deal progresses, so a column */
/* of pills reads as a heat map of the book. Closed deals show their   */
/* outcome in place of a stage.                                        */
/* ------------------------------------------------------------------ */

const STAGE_TONES: Record<string, ChipTone> = {
    Activation: "info",
    Qualification: "warn",
    "Proposal & Quote": "brand",
    Negotiation: "success",
    Won: "success",
    Lost: "danger",
};

export function StageChip({
    label,
    className,
}: Readonly<{ label: string; className?: string }>) {
    return (
        <Chip tone={STAGE_TONES[label] ?? "neutral"} className={className}>
            {label}
        </Chip>
    );
}

/* -------------------------------------------------------------- */
/* Currency: both currencies share one treatment, because the chip */
/* says which profile transacts, not whether that is good or bad.  */
/* -------------------------------------------------------------- */

export function CurrencyChip({
    currency,
    className,
}: Readonly<{ currency: string; className?: string }>) {
    return (
        <Chip tone="info" className={className}>
            {currency}
        </Chip>
    );
}
