import { cn } from "@/lib/utils";

/**
 * SegmentedControl — Sales Desk 28px segmented switch.
 *
 * Verbatim to `docs/sales-desk-designs/style-reference.md` §3:
 * `Monthly | Annual −15%` and `USD | KES | EUR | GBP` — active segment
 * filled (`brand` or `ink`), 28px tall.
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
      role="group"
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
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-full cursor-pointer rounded-md px-2.5 text-xs font-semibold whitespace-nowrap transition-colors",
              active
                ? tone === "brand"
                  ? "bg-sd-brand text-white"
                  : "bg-sd-ink text-white"
                : "text-sd-muted hover:text-sd-ink"
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
