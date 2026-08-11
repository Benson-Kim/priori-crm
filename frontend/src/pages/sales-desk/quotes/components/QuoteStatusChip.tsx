/**
 * QuoteStatusChip
 *
 * Synced means the quotations module has the quote, so the number the customer
 * sees and the number accounting holds are the same. Draft and Pending are
 * both "not there yet", which is why neither is green.
 */

import { cn } from "@/lib/utils";
import type { QuoteStatus } from "@/services/salesDeskApi";

const STATUS_CLASSES: Record<QuoteStatus, string> = {
    Draft: "bg-desk-neutral-soft text-desk-muted",
    Pending: "bg-desk-warm-soft text-desk-amber",
    Synced: "bg-desk-green-soft text-desk-green",
};

interface QuoteStatusChipProps {
    status: QuoteStatus;
    className?: string;
}

export function QuoteStatusChip({ status, className }: Readonly<QuoteStatusChipProps>) {
    return (
        <span
            className={cn(
                "inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold",
                STATUS_CLASSES[status],
                className
            )}
        >
            {status}
        </span>
    );
}
