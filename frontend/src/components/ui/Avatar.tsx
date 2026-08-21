import { cn } from "@/lib/utils";

import { avatarColor, avatarInitials } from "./avatar-utils";

/**
 * Initials disc, tinted per person so a column of them reads as a book of
 * owners rather than a column of letters.
 *
 * The colour comes from a stable hash of `name`, so a given person always
 * renders the same colour without anyone having to assign one. Pass `color`
 * to pin an exact colour instead, which is what the reps do: their colours
 * come from the record, not the hash.
 */

export type AvatarSize = 24 | 28 | 32 | 36;

/** Avatar initials: Poppins Bold 10–13 white, scaled to the avatar size. */
const SIZE_STYLES: Record<AvatarSize, string> = {
  24: "h-6 w-6 text-dense-xs",
  28: "h-7 w-7 text-dense-sm",
  32: "h-8 w-8 text-xs",
  36: "h-9 w-9 text-dense-md",
};

interface AvatarProps {
  /** Full name, or any stable identifier. Drives the initials and the colour. */
  name: string;
  /** Override the derived initials (e.g. server-provided). */
  initials?: string;
  size?: AvatarSize;
  /** Pin an exact background colour, bypassing the deterministic palette. */
  color?: string;
  className?: string;
}

export function Avatar({
  name,
  initials,
  size = 28,
  color,
  className,
}: Readonly<AvatarProps>) {
  return (
    <span
      aria-hidden="true"
      title={name}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-bold text-white select-none",
        SIZE_STYLES[size],
        className
      )}
      style={{ backgroundColor: color ?? avatarColor(name) }}
    >
      {initials ?? avatarInitials(name)}
    </span>
  );
}
