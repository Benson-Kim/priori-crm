import { cn } from "@/lib/utils";

import { avatarColor, avatarInitials } from "./avatar-utils";

/**
 * Avatar — Sales Desk shared presentational primitive.
 *
 * Circular initials avatar with the deterministic per-user palette from
 * `docs/sales-desk-designs/style-reference.md` §1 (TK `#2456E6`,
 * MW `#7C3AED`, JN `#0D9488`); white bold initials; sizes 24/28/32/36px.
 * The colour is derived from a stable hash of `name` (see avatar-utils.ts),
 * so a given user always renders the same colour. Pass `color` to pin an
 * exact colour (e.g. the signed-in user's brand `#912B90` avatar in the
 * sidebar footer / topbar, or a server-provided rep colour once available).
 */

export type AvatarSize = 24 | 28 | 32 | 36;

/** Avatar initials: Poppins Bold 10–13 white, scaled to the avatar size. */
const SIZE_STYLES: Record<AvatarSize, string> = {
  24: "h-6 w-6 text-[10px]",
  28: "h-7 w-7 text-[11px]",
  32: "h-8 w-8 text-[12px]",
  36: "h-9 w-9 text-[13px]",
};

interface AvatarProps {
  /** Full name (or stable identifier) — drives initials + palette colour. */
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
