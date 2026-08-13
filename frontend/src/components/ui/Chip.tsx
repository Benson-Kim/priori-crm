import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Chip — Sales Desk shared presentational primitives.
 *
 * Fully-rounded Poppins 600/12 pills per `docs/sales-desk-designs/
 * style-reference.md` §1/§3: status chips (Synced/Pending/Draft), stage
 * chips (per the fixed stage colour map), currency chips (USD/KES) and due
 * badges (Overdue/Today/`45d`). All colours come from the `sd-*` tokens.
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
  neutral: "bg-[#F3F4F6] text-sd-muted",
  fresh: "bg-sd-fresh-bg text-sd-fresh",
  aging: "bg-sd-aging-bg text-sd-aging",
};

interface ChipProps {
  tone: ChipTone;
  children: ReactNode;
  className?: string;
}

/** Base pill; the named chips below cover the verbatim design cases. */
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

/* ------------------------------------------------------------------ */
/* Status chips — Synced (success) / Pending (warn) / Draft (neutral) */
/* ------------------------------------------------------------------ */

export type SyncStatus = "synced" | "pending" | "draft";

const STATUS_TONES: Record<SyncStatus, ChipTone> = {
  synced: "success",
  pending: "warn",
  draft: "neutral",
};

const STATUS_LABELS: Record<SyncStatus, string> = {
  synced: "Synced",
  pending: "Pending",
  draft: "Draft",
};

export function StatusChip({
  status,
  className,
}: Readonly<{ status: SyncStatus; className?: string }>) {
  return (
    <Chip tone={STATUS_TONES[status]} className={className}>
      {STATUS_LABELS[status]}
    </Chip>
  );
}

/* -------------------------------------------------------------- */
/* Stage chips — fixed 5-stage colour map + closed Won/Lost states */
/* -------------------------------------------------------------- */

export type DealStage =
  | "Activation"
  | "Qualification"
  | "Proposal & Quote"
  | "Negotiation"
  | "Won"
  | "Lost";

const STAGE_TONES: Record<DealStage, ChipTone> = {
  Activation: "info",
  Qualification: "warn",
  "Proposal & Quote": "brand",
  Negotiation: "success",
  Won: "success",
  Lost: "danger",
};

export function StageChip({
  stage,
  className,
}: Readonly<{ stage: DealStage; className?: string }>) {
  return (
    <Chip tone={STAGE_TONES[stage]} className={className}>
      {stage}
    </Chip>
  );
}

/* ------------------------------------------- */
/* Currency chips — info-blue `USD` / `KES` …  */
/* ------------------------------------------- */

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

/* ----------------------------------------------------------- */
/* Due badges — Overdue (danger) / Today (warn) / `45d` (gray)  */
/* ----------------------------------------------------------- */

export function DueBadge({
  label,
  className,
}: Readonly<{ label: string; className?: string }>) {
  const normalized = label.trim().toLowerCase();
  const tone: ChipTone =
    normalized === "overdue"
      ? "danger"
      : normalized === "today"
        ? "warn"
        : "neutral";
  return (
    <Chip tone={tone} className={className}>
      {label}
    </Chip>
  );
}
