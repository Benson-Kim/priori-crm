import { cn } from "@/lib/utils";

/**
 * ProgressBar — Sales Desk rounded progress bar.
 *
 * Verbatim to `docs/sales-desk-designs/style-reference.md` §3:
 * - Onboarding: 6px rounded bar, brand fill (pair with "N of 7 tasks
 *   complete" + a brand-bg % chip rendered by the page).
 * - Rep quota: 8px bar filled in the rep's avatar colour.
 * Track is the `sd-border` hairline colour.
 */
interface ProgressBarProps {
  /** 0–100. Values outside the range are clamped. */
  percent: number;
  /** 6px (onboarding, default) or 8px (rep quota). */
  height?: 6 | 8;
  /** Fill colour override (e.g. the rep's avatar colour). Default: brand. */
  color?: string;
  className?: string;
  "aria-label"?: string;
}

export function ProgressBar({
  percent,
  height = 6,
  color,
  className,
  "aria-label": ariaLabel,
}: Readonly<ProgressBarProps>) {
  const clamped = Math.min(100, Math.max(0, percent));

  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={ariaLabel}
      className={cn(
        "w-full overflow-hidden rounded-full bg-sd-border",
        height === 6 ? "h-1.5" : "h-2",
        className
      )}
    >
      <div
        className={cn("h-full rounded-full", !color && "bg-sd-brand")}
        style={{ width: `${clamped}%`, ...(color ? { backgroundColor: color } : {}) }}
      />
    </div>
  );
}
