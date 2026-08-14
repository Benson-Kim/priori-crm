/**
 * Shared sidebar chrome.
 *
 * The Business Central rail and the Sales Desk rail are two components, but
 * they are the same piece of furniture and must look identical. They had
 * drifted apart — different background, active pill, text colour and item
 * metrics — so the class strings live here once and both import them.
 *
 * Colours are the pre-Sales-Desk palette: a grey rail with a white active
 * pill in brand purple. Add a token here, not in one of the two sidebars.
 */

/** The rail itself. Width is applied by the caller (collapsed vs expanded). */
export const RAIL =
    "bg-gray-100 flex flex-col h-screen shrink-0 transition-all duration-200";

/** Nav region: scrolls on its own so the footer never leaves the viewport.
 *  The scrollbar chrome is hidden — on a short viewport a permanent track
 *  down the middle of the rail reads as a bug, not an affordance. */
export const NAV_SCROLL = "flex-1 min-h-0 overflow-y-auto no-scrollbar";

/** Shared metrics for every nav row, top level or child. */
export const ITEM_BASE =
    "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors";

/** Selected row: white pill, brand purple label. */
export const ITEM_ACTIVE = "bg-white text-priori-purple font-semibold";

/** Unselected row. */
export const ITEM_IDLE = "text-gray-600 hover:bg-white/50";

/** A group header that contains the active route but is not itself a link. */
export const GROUP_ACTIVE = "text-priori-purple font-semibold";

/** Icon box. A CSS size beats the width/height attributes baked into our
 *  imported SVGs, so custom icons match Lucide's instead of rendering at
 *  their own intrinsic size. */
export const ICON = "size-[18px] shrink-0";

/** Count badge: brand circle, white bold 10. */
export const BADGE =
    "ml-auto flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-priori-purple px-1 text-[10px] font-bold text-white";

/** Footer block holding the user card and the copyright stamp. */
export const FOOTER = "mt-3 flex flex-col gap-3 border-t border-gray-300 pt-4";
