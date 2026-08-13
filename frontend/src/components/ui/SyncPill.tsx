import { cn } from "@/lib/utils";

/**
 * Whether a record has reached accounting. Dot plus label, never dot alone:
 * the word carries the meaning and the colour only reinforces it.
 */
interface SyncPillProps {
  synced: boolean;
  /** Override the default "In accounting" / "Not synced" label. */
  label?: string;
  className?: string;
}

export function SyncPill({ synced, label, className }: Readonly<SyncPillProps>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs font-medium",
        synced ? "text-sd-success" : "text-sd-faint",
        className
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "h-2 w-2 shrink-0 rounded-full",
          synced ? "bg-sd-success" : "border border-sd-faint bg-transparent"
        )}
      />
      {label ?? (synced ? "In accounting" : "Not synced")}
    </span>
  );
}
