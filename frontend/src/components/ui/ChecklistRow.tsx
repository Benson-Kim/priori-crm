import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * ChecklistRow — Sales Desk onboarding checklist row.
 *
 * Verbatim to `docs/sales-desk-designs/style-reference.md` §3: 20px
 * rounded-4 checkbox — filled brand + white check + strikethrough muted
 * label when done; gray `#C8CDD8` outline + ink label when pending;
 * right-aligned `Step N`.
 */
interface ChecklistRowProps {
  label: string;
  done: boolean;
  /** Right-aligned step counter — renders `Step N`. */
  step?: number;
  onToggle?: (done: boolean) => void;
  className?: string;
}

export function ChecklistRow({
  label,
  done,
  step,
  onToggle,
  className,
}: Readonly<ChecklistRowProps>) {
  return (
    <div className={cn("flex items-center gap-3 py-2", className)}>
      <button
        type="button"
        role="checkbox"
        aria-checked={done}
        aria-label={label}
        disabled={!onToggle}
        onClick={() => onToggle?.(!done)}
        className={cn(
          "flex h-5 w-5 shrink-0 items-center justify-center rounded transition-colors",
          done ? "bg-sd-brand" : "border border-[#C8CDD8] bg-white",
          onToggle ? "cursor-pointer" : "cursor-default"
        )}
      >
        {done && <Check size={13} strokeWidth={3} className="text-white" />}
      </button>

      <span
        className={cn(
          "flex-1 truncate text-[13px]",
          done ? "text-sd-muted line-through" : "text-sd-ink"
        )}
      >
        {label}
      </span>

      {step !== undefined && (
        <span className="shrink-0 text-xs text-sd-faint">Step {step}</span>
      )}
    </div>
  );
}
