import { lazy, Suspense, type ComponentType } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";

import { LoadingState } from "@/components/ui/LoadingState";
import LoginPage from "@/pages/auth/login";
import RequireAuth from "./auth/RequireAuth";
import DefaultLayout from "./layout/default-layout";

// Route-based code splitting: every page below is fetched on demand, so the
// initial bundle only ships the shell (router, auth guard, layout) and the
// login page. This is the single biggest lever for first paint on slow
// (3G) networks — heavy dependencies like recharts (dashboard/statements)
// and react-pdf (document previews) stay out of the entry chunk entirely.
const lazyPage = (loader: () => Promise<{ default: ComponentType }>) => {
    const Page = lazy(loader);
    return (
        <Suspense fallback={<LoadingState message="Loading..." className="h-64" />}>
            <Page />
        </Suspense>
    );
};

const routes = [
    {
        path: "/login",
        element: <LoginPage />,
    },
    {
        path: "/verify-otp",
        element: lazyPage(() => import("@/pages/auth/otp")),
    },
    {
        path: "/forgot-password",
        element: lazyPage(() => import("@/pages/auth/forgot-password")),
    },
    {
        path: "/reset-password",
        element: lazyPage(() => import("@/pages/auth/reset-password")),
    },
    {
        element: <RequireAuth />,
        children: [
            {
                path: "/",
                element: <DefaultLayout />,
                children: [
                    {
                        index: true,
                        element: <Navigate to="/dashboard" replace />,
                    },
                    {
                        path: "dashboard",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/dashboard")),
                                handle: {
                                    header: {
                                        title: "Welcome, Frank ",
                                        description: "Let's dive into your latest updates and insights.",
                                    },
                                }
                            },
                        ]
                    },
                    {
                        path: "customers",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/sales/customers")),
                                handle: {
                                    header: {
                                        title: "Customers",
                                        description: "Take a closer look at your customers.",
                                    },
                                },
                            },
                            {
                                path: "add",
                                element: lazyPage(() => import("@/pages/sales/customers/add")),
                                handle: {
                                    header: {
                                        title: "Create Customer",
                                        description: "Add customer details and manage their information.",
                                    },
                                },
                            },
                            {
                                path: ":id",
                                element: lazyPage(() => import("@/pages/sales/customers/detail")),
                                handle: {
                                    header: {
                                        title: "Customer Profile",
                                        description: "Take a closer look at your customers.",
                                    },
                                },
                            },
                            {
                                path: ":id/edit",
                                element: lazyPage(() => import("@/pages/sales/customers/edit")),
                                handle: {
                                    header: {
                                        title: "Edit Customer",
                                        description: "Update customer information and preferences.",
                                    },
                                },
                            },
                        ]
                    },
                    {
                        path: "quotes",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/sales/quotes")),
                                handle: {
                                    header: {
                                        title: "Quotes",
                                        description: "Effortlessly handle your billing and quotes here.",
                                    },
                                },
                            },
                            {
                                path: "add",
                                element: lazyPage(() => import("@/pages/sales/quotes/add")),
                                handle: {
                                    header: {
                                        title: "Create New Quote",
                                        description: "Effortlessly add your quotes here.",
                                    },
                                },
                            },
                            {
                                path: ":id",
                                element: lazyPage(() => import("@/pages/sales/quotes/detail")),
                                handle: {
                                    header: {
                                        title: "Quote Detail",
                                        description: "View complete quote information.",
                                    },
                                },
                            },
                            {
                                path: ":id/edit",
                                element: lazyPage(() => import("@/pages/sales/quotes/edit")),
                                handle: {
                                    header: {
                                        title: "Edit Quote",
                                        description: "Update quote details.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "invoices",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/sales/invoices")),
                                handle: {
                                    header: {
                                        title: "Invoices",
                                        description: "Effortlessly handle your billing and invoices here.",
                                    },
                                },
                            },
                            {
                                path: "add",
                                element: lazyPage(() => import("@/pages/sales/invoices/add")),
                                handle: {
                                    header: {
                                        title: "Create New Invoice",
                                        description: "Effortlessly add your invoices here.",
                                    },
                                },
                            },
                            {
                                path: ":id",
                                element: lazyPage(() =>
                                    import("@/pages/sales/invoices/detail").then((m) => ({
                                        default: m.InvoiceDetailPage,
                                    }))
                                ),
                                handle: {
                                    header: {
                                        title: "Invoice Detail",
                                        description: "View complete invoice information.",
                                    },
                                },
                            },
                            {
                                path: ":id/edit",
                                element: lazyPage(() => import("@/pages/sales/invoices/edit")),
                                handle: {
                                    header: {
                                        title: "Edit Invoice",
                                        description: "Update invoice details.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "vendors",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/purchases/vendors")),
                                handle: {
                                    header: {
                                        title: "Vendors",
                                        description: "Manage your suppliers and expenses.",
                                    },
                                },
                            },
                            {
                                path: ":id",
                                element: lazyPage(() => import("@/pages/purchases/vendors/detail")),
                                handle: {
                                    header: {
                                        title: "Vendor Detail",
                                        description: "View complete vendor information.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "expenses",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/purchases/expenses")),
                                handle: {
                                    header: {
                                        title: "Expenses",
                                        description: "Manage your expenses.",
                                    },
                                },
                            },
                            {
                                path: "new",
                                element: lazyPage(() => import("@/pages/purchases/expenses/add")),
                                handle: {
                                    header: {
                                        title: "Create New Expense",
                                        description: "Effortlessly add your expenses here.",
                                    },
                                },
                            },
                            {
                                path: ":id",
                                element: lazyPage(() => import("@/pages/purchases/expenses/detail")),
                                handle: {
                                    header: {
                                        title: "Expense Detail",
                                        description: "View complete expense information.",
                                    },
                                },
                            },
                            {
                                path: ":id/edit",
                                element: lazyPage(() => import("@/pages/purchases/expenses/edit")),
                                handle: {
                                    header: {
                                        title: "Edit Expense",
                                        description: "Update expense details.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "purchase-orders",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/purchases/purchase-orders")),
                                handle: {
                                    header: {
                                        title: "Purchase Orders",
                                        description: "Raise and manage purchase orders to your vendors.",
                                    },
                                },
                            },
                            {
                                path: "new",
                                element: lazyPage(() => import("@/pages/purchases/purchase-orders/add")),
                                handle: {
                                    header: {
                                        title: "Create New Purchase Order",
                                        description: "Raise a new purchase order to a vendor.",
                                    },
                                },
                            },
                            {
                                path: ":id",
                                element: lazyPage(() => import("@/pages/purchases/purchase-orders/detail")),
                                handle: {
                                    header: {
                                        title: "Purchase Order Detail",
                                        description: "View complete purchase order information.",
                                    },
                                },
                            },
                            {
                                path: ":id/edit",
                                element: lazyPage(() => import("@/pages/purchases/purchase-orders/edit")),
                                handle: {
                                    header: {
                                        title: "Edit Purchase Order",
                                        description: "Update purchase order details.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "income-statement",
                        element: lazyPage(() => import("@/pages/statements/income-statement")),
                        handle: {
                            header: {
                                title: "Income Statements",
                                description: "Explore your business's net profit; your revenues and expenses in a given time period.",
                            },
                        },
                    },
                    {
                        path: "cashflow",
                        element: lazyPage(() => import("@/pages/statements/cashflow")),
                        handle: {
                            header: {
                                title: "Cashflow",
                                description: "Explore your business's cash position; money in and money out over a given time period.",
                            },
                        },
                    },
                    {
                        path: "reports",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/reports")),
                                handle: {
                                    header: {
                                        title: "Reports",
                                        description: "Financial reports: sales, purchases, tax, and aging.",
                                    },
                                },
                            },
                            {
                                path: "sales",
                                element: lazyPage(() => import("@/pages/reports/sales")),
                                handle: {
                                    header: {
                                        title: "Sales Report",
                                        description: "Revenue, invoices, outstanding balances and aged receivables.",
                                    },
                                },
                            },
                            {
                                path: "purchases",
                                element: lazyPage(() => import("@/pages/reports/purchases")),
                                handle: {
                                    header: {
                                        title: "Purchases Report",
                                        description: "Expense and purchase order spend, vendor breakdown and aged payables.",
                                    },
                                },
                            },
                            {
                                path: "taxes",
                                element: lazyPage(() => import("@/pages/reports/taxes")),
                                handle: {
                                    header: {
                                        title: "Tax Report",
                                        description: "VAT collected vs paid — net VAT position (KES only).",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "statements",
                        children: [
                            {
                                index: true,
                                element: <Navigate to="/income-statement" replace />,
                            },
                            {
                                path: "income-statement",
                                element: <Navigate to="/income-statement" replace />,
                            },
                            {
                                path: "cashflow",
                                element: <Navigate to="/cashflow" replace />,
                            },
                        ],
                    },
                ],
            },
        ],
    },
    {
        path: '*',
        element: <div className="p-8">Page not found</div>
    },
];

const router = createBrowserRouter(routes);

export { routes };
export default router;