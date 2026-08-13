import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * KpiCard — Sales Desk dashboard KPI tile.
 *
 * Verbatim to `docs/sales-desk-designs/style-reference.md` §3: white card
 * (radius 16, hairline border, card shadow), label (uppercase 12/600/0.3px
 * muted) → value (Bold 25.6, semantic colour) → sub-line (12 muted).
 * e.g. `PIPELINE (WEIGHTED)` — $46,530 (info) — "4 open deals".
 */
export type KpiTone = "ink" | "info" | "success" | "danger";

const TONE_STYLES: Record<KpiTone, string> = {
  ink: "text-sd-ink",
  info: "text-sd-info",
  success: "text-sd-success",
  danger: "text-sd-danger",
};

interface KpiCardProps {
  label: string;
  value: string;
  /** Semantic value colour — weighted pipeline info, won success, … */
  tone?: KpiTone;
  /** Muted 12px sub-line, e.g. "4 open deals" / "Unweighted". */
  subline?: ReactNode;
  className?: string;
}

export function KpiCard({
  label,
  value,
  tone = "ink",
  subline,
  className,
}: Readonly<KpiCardProps>) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-2xl border border-sd-border bg-white p-5 shadow-sd-card",
        className
      )}
    >
      <p className="text-xs font-semibold tracking-[0.3px] text-sd-muted uppercase">
        {label}
      </p>
      <p className={cn("text-[25.6px] leading-tight font-bold", TONE_STYLES[tone])}>
        {value}
      </p>
      {subline !== undefined && (
        <p className="text-xs text-sd-muted">{subline}</p>
      )}
    </div>
  );
}
