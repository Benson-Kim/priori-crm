/**
 * Avatar palette helpers, kept separate from Avatar.tsx (like
 * button-variants.ts) so the component file only exports components
 * (eslint react-refresh/only-export-components). Pages also import these
 * directly, e.g. to colour the rep-quota ProgressBar with the rep's
 * avatar colour.
 */

/** Fallback palette for people who have no colour of their own. */
export const AVATAR_PALETTE = ["#2456E6", "#7C3AED", "#0D9488"] as const;

/** Stable string hash (djb2), so a person always maps to the same colour. */
function hashString(value: string): number {
  let hash = 5381;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 33 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

/** Deterministic palette colour for a user name/identifier. */
export function avatarColor(name: string): string {
  return AVATAR_PALETTE[hashString(name) % AVATAR_PALETTE.length];
}

/** "Tabitha Kimani" → "TK"; single names yield a single letter. */
export function avatarInitials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}
