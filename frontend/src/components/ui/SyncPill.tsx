import { cn } from "@/lib/utils";

/**
 * SyncPill — Sales Desk accounting-sync indicator.
 *
 * 8px dot + label per `docs/sales-desk-designs/style-reference.md` §3:
 * green filled ● "In accounting" when synced, gray hollow ○ "Not synced"
 * (`#9CA3AF`) otherwise. Labels can be overridden for contexts like the
 * quote builder's "Posts to BL-KES · … · ● Not synced" line.
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
