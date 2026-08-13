import {
    BarChart2,
    ClipboardList,
    DollarSign,
    FileText,
    Receipt,
    ReceiptText,
    ShoppingCart,
    Store,
    Target,
    Users
} from "lucide-react";
import type { ComponentType, SVGProps } from "react";

import Invoice from "@/assets/icons/invoice-uxwing.svg?react";
import Briefcase from "@/assets/icons/sell-svgrepo-com 1.svg?react";
import Home from "@/assets/icons/send2.svg?react";

export type NavIcon = ComponentType<
    SVGProps<SVGSVGElement> & {
        size?: number | string;
    }
>;

export type NavChild = {
    path: string;
    label: string;
    icon?: NavIcon;
    /** Module entitlement key gating this entry (absent = always visible). */
    moduleKey?: string;
    /**
     * Key into the server-driven nav badge counts (issue #45's notifications
     * endpoint). Items with a badgeKey render a 16px brand count badge when
     * the sidebar receives a positive count, and stay hidden until then.
     */
    badgeKey?: string;
}

export type NavItem =
    | { path: string; label: string; icon?: NavIcon; moduleKey?: string; children?: never }
    | { label: string; icon?: NavIcon; children: NavChild[]; path?: never; moduleKey?: never }

export const navItems: NavItem[] = [
    {
        label: "Dashboard",
        icon: Home,
        path: "/dashboard",
    },

    {
        label: "Income / Sales",
        icon: Briefcase,
        children: [
            { label: "Customers", path: "/customers", icon: Users, moduleKey: "customers" },
            { label: "Quotes", path: "/quotes", icon: FileText, moduleKey: "quotes" },
            { label: "Invoices", path: "/invoices", icon: Invoice, moduleKey: "invoices" },
        ],
    },

    /*
     * The Sales Desk is a module, not a section: following this link leaves
     * the Business Central shell for the desk's own layout and sidebar
     * (components/layout/sales-desk-layout.tsx), so it has no children here.
     */
    {
        label: "Sales Desk",
        icon: Target,
        path: "/sales-desk",
        moduleKey: "sales_desk",
    },

    {
        label: "Purchases",
        icon: ShoppingCart,
        children: [
            { label: "Vendors", path: "/vendors", icon: Store, moduleKey: "vendors" },
            { label: "Expenses", path: "/expenses", icon: Receipt, moduleKey: "expenses" },
            {
                label: "Purchase Orders",
                path: "/purchase-orders",
                icon: ClipboardList,
                moduleKey: "purchase_orders",
            },
        ],
    },

    {
        label: "Statements",
        icon: ReceiptText,
        children: [
            {
                label: "Income Statement",
                path: "/income-statement",
                icon: FileText,
                moduleKey: "statements",
            },
            {
                label: "Cashflow",
                path: "/cashflow",
                icon: DollarSign,
                moduleKey: "statements",
            },
        ],
    },

    {
        label: "Reports",
        icon: BarChart2,
        children: [
            { label: "Sales Report", path: "/reports/sales", icon: FileText, moduleKey: "reports" },
            { label: "Purchases Report", path: "/reports/purchases", icon: Receipt, moduleKey: "reports" },
            { label: "Tax Report", path: "/reports/taxes", icon: ReceiptText, moduleKey: "reports" },
        ],
    },
];

/**
 * Filter the nav tree by the per-owner module entitlement map.
 *
 * Entries without a moduleKey are always visible; a null/undefined map
 * (bootstrap still loading, or older API) shows everything (fail-open,
 * mirroring the backend's missing-row-=-enabled default). Groups whose
 * children are ALL hidden disappear entirely.
 */
export function visibleNavItems(
    enabledModules: Record<string, boolean> | null | undefined
): NavItem[] {
    const isEnabled = (moduleKey?: string) =>
        !moduleKey || !enabledModules || enabledModules[moduleKey] !== false;

    const result: NavItem[] = [];
    for (const item of navItems) {
        if (!item.children) {
            if (isEnabled(item.moduleKey)) {
                result.push(item);
            }
            continue;
        }
        const children = item.children.filter((child) =>
            isEnabled(child.moduleKey)
        );
        if (children.length > 0) {
            result.push({ ...item, children });
        }
    }
    return result;
}
