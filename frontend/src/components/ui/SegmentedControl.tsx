import { cn } from "@/lib/utils";

/**
 * A small set of mutually exclusive choices shown side by side, for switches
 * that change how the same figures are expressed rather than which records
 * are shown: billing period, display currency.
 */
export interface SegmentedOption {
  value: string;
  label: string;
}

interface SegmentedControlProps {
  options: SegmentedOption[];
  value: string;
  onChange: (value: string) => void;
  /** Active-segment fill: `ink` (billing cycle) or `brand`. */
  tone?: "ink" | "brand";
  className?: string;
  "aria-label"?: string;
}

export function SegmentedControl({
  options,
  value,
  onChange,
  tone = "ink",
  className,
  "aria-label": ariaLabel,
}: Readonly<SegmentedControlProps>) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex h-7 items-center rounded-lg border border-sd-border bg-sd-surface p-0.5",
        className
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-full cursor-pointer rounded-md px-2.5 text-xs font-semibold whitespace-nowrap",
              "transition-colors duration-150 active:scale-[0.98]",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sd-brand",
              active
                ? tone === "brand"
                  ? "bg-sd-brand text-white"
                  : "bg-sd-ink text-white"
                : "text-sd-muted hover:bg-sd-card hover:text-sd-ink"
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
