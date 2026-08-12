import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * One step of a checklist: a tick box, the step name, and its position in the
 * sequence.
 *
 * A completed step is struck through as well as ticked, so the state does not
 * rest on the tick alone.
 *
 * The control is a real checkbox, visually hidden and driven by the styled box
 * beside it, so the row keeps native keyboard and screen-reader behaviour
 * rather than reimplementing it.
 */
interface ChecklistRowProps {
    /** Unique id for the input, so the label can point at it. */
    id: string;
    label: string;
    done: boolean;
    /** Position in the sequence, rendered as `Step N`. */
    step?: number;
    disabled?: boolean;
    onToggle: (done: boolean) => void;
    className?: string;
}

export function ChecklistRow({
    id,
    label,
    done,
    step,
    disabled = false,
    onToggle,
    className,
}: Readonly<ChecklistRowProps>) {
    return (
        <li
            className={cn(
                // `relative` so the visually-hidden input below, which is
                // absolutely positioned, resolves against this row. Without a
                // positioned ancestor it lays out against the document and
                // stretches the page past the scroll container.
                "relative flex items-center gap-3 border-b border-sd-border px-5 py-3 last:border-b-0",
                className
            )}
        >
            <input
                type="checkbox"
                id={id}
                className="peer sr-only"
                checked={done}
                disabled={disabled}
                onChange={(event) => onToggle(event.target.checked)}
            />
            {/*
             * The focus ring is declared here, on the input's sibling, and
             * reaches down to the box. `peer-*` only matches siblings of the
             * peer, so a ring declared on the box itself would never fire.
             */}
            <label
                htmlFor={id}
                className={cn(
                    "flex flex-1 cursor-pointer items-center gap-3 select-none",
                    "peer-focus-visible:[&_[data-box]]:ring-2",
                    "peer-focus-visible:[&_[data-box]]:ring-sd-brand/40"
                )}
            >
                <span
                    data-box
                    aria-hidden="true"
                    className={cn(
                        "flex size-5 shrink-0 items-center justify-center rounded border transition-colors",
                        done ? "border-sd-brand bg-sd-brand" : "border-gray-300 bg-sd-card"
                    )}
                >
                    {done && <Check className="size-3.5 text-white" strokeWidth={3} />}
                </span>
                <span
                    className={cn(
                        "flex-1 text-[13px]",
                        done ? "text-sd-muted line-through" : "text-sd-ink"
                    )}
                >
                    {label}
                </span>
                {step !== undefined && (
                    <span className="shrink-0 text-xs text-sd-muted">Step {step}</span>
                )}
            </label>
        </li>
    );
}
