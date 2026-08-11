/**
 * DeskDrawer
 *
 * Right-hand overlay panel shared by the pipeline and companies screens. It
 * sits over the list rather than beside it, so the table never reflows when a
 * row is selected.
 *
 * Non-modal and un-scrimmed, so rows underneath stay live and clicking a
 * different one swaps the panel straight over. That makes the accessibility
 * contract unusual: focus moves into the panel on open and returns on close,
 * but is not trapped, and Escape closes since there is no scrim to click away.
 */

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface DeskDrawerProps {
    /** Accessible name for the panel, usually the record it describes. */
    label: string;
    onClose: () => void;
    /** Rendered inside the sticky header, above the hairline. */
    header: ReactNode;
    children: ReactNode;
    className?: string;
}

export function DeskDrawer({
    label,
    onClose,
    header,
    children,
    className,
}: Readonly<DeskDrawerProps>) {
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
                "border-l border-desk-border bg-desk-surface shadow-[-8px_0_32px_rgba(0,0,0,0.10)]",
                "focus:outline-none",
                className
            )}
        >
            <div className="flex items-start gap-3 border-b border-desk-border p-4">
                <div className="min-w-0 flex-1">{header}</div>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label={`Close ${label}`}
                    className="rounded p-1 text-desk-muted transition-colors hover:text-desk-ink"
                >
                    <X className="size-4" aria-hidden="true" />
                </button>
            </div>

            <div className="flex flex-col gap-4 p-4">{children}</div>
        </aside>
    );
}

/**
 * Uppercase micro-label that opens each drawer section
 * (BILLING PROFILES, STAGE RECORD, DEALS).
 */
export function DeskSectionLabel({ children }: Readonly<{ children: ReactNode }>) {
    return (
        <p className="text-[10px] font-bold tracking-[1px] text-desk-muted uppercase">
            {children}
        </p>
    );
}
