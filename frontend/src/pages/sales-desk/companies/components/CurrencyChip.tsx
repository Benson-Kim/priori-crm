/**
 * CurrencyChip
 *
 * A billing currency as a badge. Both currencies share one treatment: the chip
 * says which profile transacts, not whether that is good or bad.
 */

import { cn } from "@/lib/utils";
import type { BillingCurrency } from "@/services/salesDeskApi";

interface CurrencyChipProps {
    currency: BillingCurrency;
    className?: string;
}

export function CurrencyChip({ currency, className }: Readonly<CurrencyChipProps>) {
    return (
        <span
            className={cn(
                "inline-block rounded-full bg-desk-blue-soft px-2 py-0.5 text-xs font-semibold text-desk-blue",
                className
            )}
        >
            {currency}
        </span>
    );
}
