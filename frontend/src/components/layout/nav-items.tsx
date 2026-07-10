import {
    ReceiptText,
    ShoppingCart,
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
            { label: "Customers", path: "/customers" },
            { label: "Quotes", path: "/quotes" },
            { label: "Invoices", path: "/invoices", icon: Invoice },
        ],
    },

    {
        label: "Purchases",
        icon: ShoppingCart,
        children: [
            { label: "Vendors", path: "/vendors" },
            { label: "Expenses", path: "/expenses" },
            { label: "Purchase Orders", path: "/purchase-orders" },
        ],
    },

    {
        label: "Statements",
        icon: ReceiptText,
        children: [
            { label: "Income Statement", path: "/income-statement" },
            { label: "Cashflow", path: "/cashflow" },
        ],
    },
];