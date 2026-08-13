import { cn } from "@/lib/utils";

/**
 * A proportion as a filled track.
 *
 * The bar is never the only carrier of the number: callers pair it with the
 * count or percentage in text, and the bar itself reports the value through
 * `role="progressbar"`.
 */
interface ProgressBarProps {
  /** 0 to 100. Values outside the range are clamped, since a rep can beat quota. */
  percent: number;
  /** 6px for checklists, 8px for the heavier quota rows. */
  height?: 6 | 8;
  /** Raw CSS fill colour, e.g. a rep's own colour. Defaults to brand. */
  color?: string;
  className?: string;
  "aria-label"?: string;
}

/**
 * A share this small would round to nothing, so it is floored to a hairline:
 * "barely any" and "none at all" have to look different.
 */
const MIN_VISIBLE_PERCENT = 1.5;

export function ProgressBar({
  percent,
  height = 6,
  color,
  className,
  "aria-label": ariaLabel,
}: Readonly<ProgressBarProps>) {
  const clamped = Math.min(100, Math.max(0, percent));
  const width = clamped > 0 ? Math.max(clamped, MIN_VISIBLE_PERCENT) : 0;

  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
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
        className={cn(
          "h-full rounded-full transition-[width] duration-300",
          !color && "bg-sd-brand"
        )}
        style={{ width: `${width}%`, ...(color ? { backgroundColor: color } : {}) }}
      />
    </div>
  );
}
