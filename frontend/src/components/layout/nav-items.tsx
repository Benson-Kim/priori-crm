import {
    ChartLine,
    ClipboardList,
    DollarSign,
    FileText,
    Landmark,
    Receipt,
    ReceiptText,
    ShoppingCart,
    Store,
    TrendingUp,
    Users,
} from "lucide-react";

import Invoice from "@/assets/icons/invoice-uxwing.svg?react";
import Briefcase from "@/assets/icons/sell-svgrepo-com 1.svg?react";
import Home from "@/assets/icons/send2.svg?react";

export const navItems = [
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
        label: "Reports",
        icon: ChartLine,
        children: [
            { label: "Sales", path: "/reports/sales", icon: TrendingUp },
            { label: "Expenses", path: "/reports/expenses", icon: ReceiptText },
            { label: "Taxes", path: "/reports/taxes", icon: Landmark },
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