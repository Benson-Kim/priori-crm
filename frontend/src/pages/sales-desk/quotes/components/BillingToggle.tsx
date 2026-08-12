/**
 * BillingToggle
 *
 * Monthly or annual billing for one quote line. The discount is named on the
 * control itself rather than being discovered afterwards in the line total.
 */

import { cn } from "@/lib/utils";
import { ANNUAL_DISCOUNT, type BillingPeriod } from "@/services/salesDeskApi";

interface BillingToggleProps {
    value: BillingPeriod;
    onChange: (value: BillingPeriod) => void;
    /** Names the line this control belongs to, for screen readers. */
    lineLabel: string;
}

const OPTIONS: Array<{ value: BillingPeriod; label: string }> = [
    { value: "monthly", label: "Monthly" },
    { value: "annual", label: `Annual −${Math.round(ANNUAL_DISCOUNT * 100)}%` },
];

export function BillingToggle({ value, onChange, lineLabel }: Readonly<BillingToggleProps>) {
    return (
        <div
            role="radiogroup"
            aria-label={`Billing period for ${lineLabel}`}
            className="inline-flex overflow-hidden rounded-lg border border-desk-border"
        >
            {OPTIONS.map((option) => {
                const isActive = option.value === value;
                return (
                    <button
                        key={option.value}
                        type="button"
                        role="radio"
                        aria-checked={isActive}
                        onClick={() => onChange(option.value)}
                        className={cn(
                            "px-2.5 py-1.5 text-[11px] font-semibold whitespace-nowrap transition-colors",
                            isActive
                                ? option.value === "annual"
                                    ? "bg-priori-purple text-white"
                                    : "bg-desk-ink text-white"
                                : "bg-desk-surface text-desk-muted hover:text-desk-ink"
                        )}
                    >
                        {option.label}
                    </button>
                );
            })}
        </div>
    );
}
