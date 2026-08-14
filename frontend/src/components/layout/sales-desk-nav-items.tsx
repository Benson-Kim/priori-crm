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

import CompaniesIcon from "@/assets/icons/sales-desk/companies.svg?react";
import DashboardIcon from "@/assets/icons/sales-desk/dashboard.svg?react";
import FuturePipelineIcon from "@/assets/icons/sales-desk/future-pipeline.svg?react";
import OnboardingIcon from "@/assets/icons/sales-desk/onboarding.svg?react";
import PipelineIcon from "@/assets/icons/sales-desk/pipeline.svg?react";
import QuotesIcon from "@/assets/icons/sales-desk/quotes.svg?react";

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
    /**
     * Module entitlement key gating this entry (absent = always visible).
     * Dashboard and Companies ride on the `sales_desk` key that gates the
     * whole subtree; the entries backed by their own routers carry their
     * own keys, matching the backend's per-router gates.
     */
    moduleKey?: string;
}

/** Root of the module, and the entry point from the Business Central sidebar. */
export const SALES_DESK_ROOT = "/sales-desk";

export const salesDeskNavItems: SalesDeskNavItem[] = [
    { label: "Dashboard", path: SALES_DESK_ROOT, icon: DashboardIcon, end: true },
    {
        label: "Pipeline",
        path: `${SALES_DESK_ROOT}/pipeline`,
        icon: PipelineIcon,
        moduleKey: "deals",
    },
    { label: "Companies", path: `${SALES_DESK_ROOT}/companies`, icon: CompaniesIcon },
    {
        label: "Future pipeline",
        path: `${SALES_DESK_ROOT}/future-pipeline`,
        icon: FuturePipelineIcon,
        moduleKey: "nurture",
    },
    {
        label: "Quotes & pricing",
        path: `${SALES_DESK_ROOT}/quotes`,
        icon: QuotesIcon,
        moduleKey: "quotes",
    },
    {
        label: "Onboarding",
        path: `${SALES_DESK_ROOT}/onboarding`,
        icon: OnboardingIcon,
        moduleKey: "onboarding",
    },
];

/**
 * Filter the desk nav by the per-owner module entitlement map, the same
 * fail-open rule `visibleNavItems` applies to the Business Central sidebar
 * (finding 07): entries without a moduleKey are always visible, and a
 * null/undefined map (bootstrap still loading, or older API) shows
 * everything, mirroring the backend's missing-row-=-enabled default.
 */
export function visibleSalesDeskNavItems(
    enabledModules: Record<string, boolean> | null | undefined
): SalesDeskNavItem[] {
    return salesDeskNavItems.filter(
        ({ moduleKey }) =>
            !moduleKey || !enabledModules || enabledModules[moduleKey] !== false
    );
}
