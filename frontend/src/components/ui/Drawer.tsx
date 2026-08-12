import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Right-hand detail panel: 390px, hairline-divided sections under uppercase
 * micro-labels, shadowed along its left edge.
 *
 * It sits over the list rather than beside it, so the table never reflows when
 * a row is selected.
 *
 * Deliberately non-modal and un-scrimmed. Rows underneath stay live, so
 * clicking a different one swaps the panel straight over, and a keyboard user
 * can keep scanning the table with the panel open. That makes the
 * accessibility contract unusual: focus moves into the panel on open and
 * returns on close, but is not trapped, and there is no `aria-modal`, since
 * the rest of the page is genuinely still available. Escape closes, because
 * there is no scrim to click away.
 */

/** Uppercase micro-label that opens each drawer section. */
export function MicroLabel({
    children,
    className,
}: Readonly<{ children: ReactNode; className?: string }>) {
    return (
        <p
            className={cn(
                "text-[10px] font-bold tracking-[1px] text-sd-muted uppercase",
                className
            )}
        >
            {children}
        </p>
    );
}

/** Micro-labelled drawer section, divided by hairlines. */
export function DrawerSection({
    label,
    children,
    className,
}: Readonly<{ label?: string; children: ReactNode; className?: string }>) {
    return (
        <section
            className={cn(
                "flex flex-col gap-2.5 border-b border-sd-border px-5 py-4 last:border-b-0",
                className
            )}
        >
            {label && <MicroLabel>{label}</MicroLabel>}
            {children}
        </section>
    );
}

interface DrawerProps {
    /** Accessible name for the panel, usually the record it describes. */
    label: string;
    onClose: () => void;
    /** Rendered in the sticky header, beside the close button. */
    header: ReactNode;
    /** Compose from `DrawerSection`s. */
    children: ReactNode;
    className?: string;
}

export function Drawer({
    label,
    onClose,
    header,
    children,
    className,
}: Readonly<DrawerProps>) {
    const panelRef = useRef<HTMLElement>(null);

    useEffect(() => {
        const previouslyFocused = document.activeElement as HTMLElement | null;
        // Captured now: by cleanup time the ref may already point elsewhere.
        const panel = panelRef.current;
        panel?.focus();

        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKeyDown);

        return () => {
            window.removeEventListener("keydown", onKeyDown);
            // Only reclaim focus if it is still inside the panel; the user may
            // have deliberately moved on to the list behind it.
            if (panel?.contains(document.activeElement)) {
                previouslyFocused?.focus?.();
            }
        };
    }, [onClose]);

    return (
        <aside
            ref={panelRef}
            tabIndex={-1}
            aria-label={label}
            className={cn(
                "fixed inset-y-0 right-0 z-40 flex w-full max-w-[390px] flex-col overflow-y-auto",
                "border-l border-sd-border bg-sd-card shadow-sd-drawer",
                "focus:outline-none",
                className
            )}
        >
            <div className="flex items-start gap-3 border-b border-sd-border px-5 py-4">
                <div className="min-w-0 flex-1">{header}</div>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label={`Close ${label}`}
                    className="shrink-0 cursor-pointer rounded p-1 text-sd-muted transition-colors hover:bg-sd-surface hover:text-sd-ink"
                >
                    <X size={18} aria-hidden="true" />
                </button>
            </div>

            <div className="flex flex-1 flex-col">{children}</div>
        </aside>
    );
}
