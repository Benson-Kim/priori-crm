import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A single headline number, its name, and the qualifier that keeps it honest:
 * "$46,530" means little until "4 open deals" sits under it.
 *
 * The tone colours the value, not the card, so a row of tiles stays calm.
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
  /** Colours the figure, not the card. */
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
      <p className={cn("text-2xl leading-tight font-bold", TONE_STYLES[tone])}>
        {value}
      </p>
      {subline !== undefined && (
        <p className="text-xs text-sd-muted">{subline}</p>
      )}
    </div>
  );
}
