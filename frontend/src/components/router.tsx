import { lazy, Suspense, type ComponentType } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";

import { LoadingState } from "@/components/ui/LoadingState";
import LoginPage from "@/pages/auth/login";
import RequireAuth from "./auth/RequireAuth";
import RequireModule from "./auth/RequireModule";
import DefaultLayout from "./layout/default-layout";
import SalesDeskLayout from "./layout/sales-desk-layout";
import SettingsLayout from "./layout/settings-layout";

// Route-based code splitting: every page below is fetched on demand, so the
// initial bundle only ships the shell (router, auth guard, layout) and the
// login page. This is the single biggest lever for first paint on slow
// (3G) networks: heavy dependencies like recharts (dashboard/statements)
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
                        element: <RequireModule moduleKey="customers" />,
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
                        element: <RequireModule moduleKey="quotes" />,
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
                        element: <RequireModule moduleKey="invoices" />,
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
                        element: <RequireModule moduleKey="vendors" />,
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
                        element: <RequireModule moduleKey="expenses" />,
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
                        element: <RequireModule moduleKey="purchase_orders" />,
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
                        // Both statement pages share the single 'statements'
                        // module key via one pathless layout guard.
                        element: <RequireModule moduleKey="statements" />,
                        children: [
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
                        ],
                    },
                    {
                        path: "reports",
                        element: <RequireModule moduleKey="reports" />,
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
                                        description: "VAT collected vs paid — net VAT position (KES ONLY).",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        // Owner Settings section (issue #58 / ADR-0011):
                        // business details + branding, document defaults, and
                        // the READ-ONLY module entitlements granted by the
                        // platform operator.
                        path: "settings",
                        element: <SettingsLayout />,
                        children: [
                            {
                                index: true,
                                element: <Navigate to="/settings/organisation" replace />,
                            },
                            {
                                path: "organisation",
                                element: lazyPage(() => import("@/pages/settings/organisation")),
                                handle: {
                                    header: {
                                        title: "Settings",
                                        description: "Business details, branding and logo for your organisation.",
                                    },
                                },
                            },
                            {
                                path: "documents",
                                element: lazyPage(() => import("@/pages/settings/documents")),
                                handle: {
                                    header: {
                                        title: "Settings",
                                        description: "Organisation-wide document defaults.",
                                    },
                                },
                            },
                            {
                                path: "modules",
                                element: lazyPage(() => import("@/pages/settings/modules")),
                                handle: {
                                    header: {
                                        title: "Settings",
                                        description: "Modules granted to your organisation by the platform operator.",
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
            /*
             * Sales Desk, a sibling shell, not a section of DefaultLayout.
             * The module owns the whole /sales-desk subtree and swaps in its
             * own sidebar and header, so it mounts alongside the Business
             * Central layout under the same auth guard.
             */
            {
                path: "sales-desk",
                element: <RequireModule moduleKey="sales_desk" />,
                children: [
                    {
                        element: <SalesDeskLayout />,
                        children: [
                    {
                        index: true,
                        element: lazyPage(() => import("@/pages/sales-desk")),
                        handle: {
                            header: {
                                title: "Dashboard",
                                description: "Pipeline health, bookings and quota attainment.",
                            },
                        },
                    },
                    /*
                     * The desk's sub-destinations are backed by their own
                     * gated routers (deals, nurture, quotes, onboarding), so
                     * each group carries the matching route guard — the same
                     * mechanism the Business Central routes use — landing a
                     * disabled module's URLs on the desk dashboard instead of
                     * the backend's 403 (finding 07). Dashboard and Companies
                     * ride on the sales_desk gate around the whole subtree.
                     */
                    {
                        path: "pipeline",
                        element: <RequireModule moduleKey="deals" redirectTo="/sales-desk" />,
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/sales-desk/pipeline")),
                                handle: {
                                    header: {
                                        title: "Pipeline",
                                        description: "Every deal and what it is worth.",
                                    },
                                },
                            },
                            {
                                path: "workspace",
                                element: lazyPage(
                                    () => import("@/pages/sales-desk/pipeline/workspace")
                                ),
                                handle: {
                                    header: {
                                        title: "Pipeline",
                                        description: "Work deals by owner, stage and activity.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "companies",
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/sales-desk/companies")),
                                handle: {
                                    header: {
                                        title: "Companies",
                                        description: "Who we sell to and how to reach them.",
                                    },
                                },
                            },
                            {
                                path: "workspace",
                                element: lazyPage(
                                    () => import("@/pages/sales-desk/companies/workspace")
                                ),
                                handle: {
                                    header: {
                                        title: "Companies",
                                        description: "Billing profiles and accounting sync.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "future-pipeline",
                        element: <RequireModule moduleKey="nurture" redirectTo="/sales-desk" />,
                        children: [
                            {
                                index: true,
                                element: lazyPage(
                                    () => import("@/pages/sales-desk/future-pipeline")
                                ),
                                handle: {
                                    header: {
                                        title: "Future pipeline",
                                        description: "Prospects and why we are waiting.",
                                    },
                                },
                            },
                            {
                                path: "workspace",
                                element: lazyPage(
                                    () => import("@/pages/sales-desk/future-pipeline/workspace")
                                ),
                                handle: {
                                    header: {
                                        title: "Future pipeline",
                                        description: "Planned prospects with engage-by dates.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "quotes",
                        element: <RequireModule moduleKey="quotes" redirectTo="/sales-desk" />,
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/sales-desk/quotes")),
                                handle: {
                                    header: {
                                        title: "Quotes & pricing",
                                        description:
                                            "Active quotes and the partner price list.",
                                    },
                                },
                            },
                            {
                                path: "new",
                                element: lazyPage(() => import("@/pages/sales-desk/quotes/new")),
                                handle: {
                                    header: {
                                        title: "Quotes & pricing",
                                        description:
                                            "Per-user pricing, billing period and currency.",
                                    },
                                },
                            },
                        ],
                    },
                    {
                        path: "onboarding",
                        element: (
                            <RequireModule moduleKey="onboarding" redirectTo="/sales-desk" />
                        ),
                        children: [
                            {
                                index: true,
                                element: lazyPage(() => import("@/pages/sales-desk/onboarding")),
                                handle: {
                                    header: {
                                        title: "Onboarding",
                                        description:
                                            "Post-sale delivery checklists for won deals.",
                                    },
                                },
                            },
                        ],
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
