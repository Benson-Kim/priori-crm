/**
 * SyncStatus
 *
 * Whether a billing profile has reached accounting. Dot plus label, never dot
 * alone: the word carries the meaning and the colour only reinforces it.
 */

import { cn } from "@/lib/utils";

interface SyncStatusProps {
    synced: boolean;
    className?: string;
}

export function SyncStatus({ synced, className }: Readonly<SyncStatusProps>) {
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1.5 text-xs whitespace-nowrap",
                synced ? "text-desk-green" : "text-desk-muted",
                className
            )}
        >
            <span
                className={cn(
                    "size-2 shrink-0 rounded-full",
                    synced ? "bg-desk-green" : "bg-gray-400"
                )}
                aria-hidden="true"
            />
            {synced ? "In accounting" : "Not synced"}
        </span>
    );
}
