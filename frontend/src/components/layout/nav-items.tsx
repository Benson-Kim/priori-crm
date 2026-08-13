import {
    BarChart2,
    ClipboardList,
    DollarSign,
    FileText,
    Receipt,
    ReceiptText,
    ShoppingCart,
    Store,
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
}

export type NavItem =
    | { path: string; label: string; icon?: NavIcon; children?: never }
    | { label: string; icon?: NavIcon; children: NavChild[]; path?: never }

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

    {
        label: "Purchases",
        icon: ShoppingCart,
        children: [
            { label: "Vendors", path: "/vendors", icon: Store },
            { label: "Expenses", path: "/expenses", icon: Receipt },
            {
                label: "Purchase Orders",
                path: "/purchase-orders",
                icon: ClipboardList,
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
            },
            { label: "Cashflow", path: "/cashflow", icon: DollarSign },
        ],
    },

    {
        label: "Reports",
        icon: BarChart2,
        children: [
            { label: "Sales Report", path: "/reports/sales", icon: FileText },
            { label: "Purchases Report", path: "/reports/purchases", icon: Receipt },
            { label: "Tax Report", path: "/reports/taxes", icon: ReceiptText },
        ],
    },
];Report", path: "/reports/taxes", icon: ReceiptText },
        ],
    },
];Entries without a moduleKey are always visible; a null/undefined map
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