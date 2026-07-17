import {
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
];