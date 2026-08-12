/**
 * Sales Desk module navigation.
 *
 * The Sales Desk runs inside its own shell (see `sales-desk-layout.tsx`), so it
 * carries a flat nav of its own rather than another group inside the Business
 * Central sidebar in `nav-items.tsx`.
 *
 * Icons under `assets/icons/sales-desk/` stroke with `currentColor`, so the
 * active and inactive colours come from the link's text colour.
 */

import type { ComponentType, SVGProps } from "react";

import DashboardIcon from "@/assets/icons/sales-desk/dashboard.svg?react";
import PipelineIcon from "@/assets/icons/sales-desk/pipeline.svg?react";

export type SalesDeskNavIcon = ComponentType<SVGProps<SVGSVGElement>>;

export interface SalesDeskNavItem {
    /** Absolute path, the Sales Desk owns the whole /sales-desk subtree. */
    path: string;
    label: string;
    icon: SalesDeskNavIcon;
    /**
     * Match the path exactly instead of by prefix. Only the module index needs
     * this, since every other route starts with "/sales-desk".
     */
    end?: boolean;
}

/** Root of the module, and the entry point from the Business Central sidebar. */
export const SALES_DESK_ROOT = "/sales-desk";

export const salesDeskNavItems: SalesDeskNavItem[] = [
    { label: "Dashboard", path: SALES_DESK_ROOT, icon: DashboardIcon, end: true },
    { label: "Pipeline", path: `${SALES_DESK_ROOT}/pipeline`, icon: PipelineIcon },
];
