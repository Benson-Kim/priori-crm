import {
    BarChart2,
    Building2,
    CalendarClock,
    ClipboardCheck,
    ClipboardList,
    DollarSign,
    FileText,
    LayoutDashboard,
    Receipt,
    ReceiptText,
    ShoppingCart,
    Store,
    Tags,
    TrendingUp,
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
    /**
     * Key into the server-driven nav badge counts (issue #45's notifications
     * endpoint). Items with a badgeKey render a 16px brand count badge when
     * the sidebar receives a positive count — hidden until then.
     */
    badgeKey?: string;
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
        label: "Sales Desk",
        icon: TrendingUp,
        children: [
            {
                label: "Dashboard",
                path: "/sales-desk/dashboard",
                icon: LayoutDashboard,
            },
            { label: "Pipeline", path: "/sales-desk/pipeline", icon: TrendingUp },
            {
                label: "Companies",
                path: "/sales-desk/companies",
                icon: Building2,
                badgeKey: "companies",
            },
            {
                label: "Future pipeline",
                path: "/sales-desk/future-pipeline",
                icon: CalendarClock,
                badgeKey: "future_pipeline",
            },
            {
                label: "Quotes & pricing",
                path: "/sales-desk/quotes",
                icon: Tags,
            },
            {
                label: "Onboarding",
                path: "/sales-desk/onboarding",
                icon: ClipboardCheck,
            },
        ],
    },

    {
        label: "Income / Sales",
        icon: Briefcase,
        children: [
            { label: "Customers", path: "/customers", icon: Users },
            { label: "Quotes", path: "/quotes", icon: FileText },
            { label: "Invoices", path: "/invoices", icon: Invoice },
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
];