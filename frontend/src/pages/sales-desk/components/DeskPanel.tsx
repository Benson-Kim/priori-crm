/**
 * DeskPanel
 *
 * The card shell every block on the desk dashboard sits on. `bleed` drops the
 * body padding for content that runs edge to edge, such as a table.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface DeskPanelProps {
    title: string;
    children: ReactNode;
    className?: string;
    bleed?: boolean;
}

export function DeskPanel({ title, children, className, bleed = false }: Readonly<DeskPanelProps>) {
    return (
        <section
            className={cn(
                "overflow-hidden rounded-2xl border border-desk-border bg-desk-surface shadow-[0_1px_1.5px_rgba(0,0,0,0.06)]",
                bleed ? "" : "p-5",
                className
            )}
        >
            <h2
                className={cn(
                    "text-[13px] font-semibold text-desk-ink",
                    bleed && "border-b border-desk-border px-5 py-4"
                )}
            >
                {title}
            </h2>
            <div className={cn(!bleed && "pt-4")}>{children}</div>
        </section>
    );
}
