/**
 * RepAvatar
 *
 * Initials disc tinted with the rep's own colour, so a book of deals is
 * scannable by owner without reading names.
 */

import { cn } from "@/lib/utils";

interface RepAvatarProps {
    initials: string;
    /** Raw CSS colour from the rep record, not a Tailwind token. */
    color: string;
    size?: "sm" | "md" | "lg";
    className?: string;
}

const SIZE_CLASSES = {
    sm: "size-6 text-[10px]",
    md: "size-7 text-[11px]",
    lg: "size-8.5 text-xs",
} as const;

export function RepAvatar({ initials, color, size = "md", className }: Readonly<RepAvatarProps>) {
    return (
        <span
            className={cn(
                "flex shrink-0 items-center justify-center rounded-full font-bold text-white",
                SIZE_CLASSES[size],
                className
            )}
            style={{ backgroundColor: color }}
            aria-hidden="true"
        >
            {initials}
        </span>
    );
}
