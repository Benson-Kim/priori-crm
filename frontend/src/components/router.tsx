import ExpensesPage from "@/pages/purchases/expenses";
import AddExpensePage from "@/pages/purchases/expenses/add";
import ExpensesDetailPage from "@/pages/purchases/expenses/detail";
import EditExpensePage from "@/pages/purchases/expenses/edit";
import VendorsPage from "@/pages/purchases/vendors";
import VendorDetailPage from "@/pages/purchases/vendors/detail";
import CustomersPage from "@/pages/sales/customers";
import AddCustomerPage from "@/pages/sales/customers/add";
import CustomerDetailsPage from "@/pages/sales/customers/detail";
import EditCustomerPage from "@/pages/sales/customers/edit";
import InvoicesPage from "@/pages/sales/invoices";
import AddInvoicePage from "@/pages/sales/invoices/add";
import { InvoiceDetailPage } from "@/pages/sales/invoices/detail";
import EditInvoicePage from "@/pages/sales/invoices/edit";
import QuotesPage from "@/pages/sales/quotes";
import AddQuotePage from "@/pages/sales/quotes/add";
import QuoteDetailPage from "@/pages/sales/quotes/detail";
import EditQuotePage from "@/pages/sales/quotes/edit";
import LoginPage from "@/pages/auth/login";
import OTPPage from "@/pages/auth/otp";
import { createBrowserRouter, Navigate } from "react-router-dom";
import RequireAuth from "./auth/RequireAuth";
import DefaultLayout from "./layout/default-layout";



const routes = [
    {
        path: "/login",
        element: <LoginPage />,
    },
    {
        path: "/verify-otp",
        element: <OTPPage />,
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
                element: <Navigate to="/customers" replace />,
            },
            {
                path: "customers",
                children: [
                    {
                        index: true,
                        element: <CustomersPage />,
                        handle: {
                            header: {
                                title: "Customers",
                                description: "Take a closer look at your customers.",
                            },
                        },
                    },
                    {
                        path: "add",
                        element: <AddCustomerPage />,
                        handle: {
                            header: {
                                title: "Create Customer",
                                description: "Add customer details and manage their information.",
                            },
                        },
                    },
                    {
                        path: ":id",
                        element: <CustomerDetailsPage />,
                        handle: {
                            header: {
                                title: "Customer Profile",
                                description: "Take a closer look at your customers.",
                            },
                        },
                    },
                    {
                        path: ":id/edit",
                        element: <EditCustomerPage />,
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
                        element: <QuotesPage />,
                        handle: {
                            header: {
                                title: "Quotes",
                                description: "Effortlessly handle your billing and quotes here.",
                            },
                        },
                    },
                    {
                        path: "add",
                        element: <AddQuotePage />,
                        handle: {
                            header: {
                                title: "Create New Quote",
                                description: "Effortlessly add your quotes here.",
                            },
                        },
                    },
                    {
                        path: ":id",
                        element: <QuoteDetailPage />,
                        handle: {
                            header: {
                                title: "Quote Detail",
                                description: "View complete quote information.",
                            },
                        },
                    },
                    {
                        path: ":id/edit",
                        element: <EditQuotePage />,
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
                        element: <InvoicesPage />,
                        handle: {
                            header: {
                                title: "Invoices",
                                description: "Effortlessly handle your billing and invoices here.",
                            },
                        },
                    },
                    {
                        path: "add",
                        element: <AddInvoicePage />,
                        handle: {
                            header: {
                                title: "Create New Invoice",
                                description: "Effortlessly add your invoices here.",
                            },
                        },
                    },
                    {
                        path: ":id",
                        element: <InvoiceDetailPage />,
                        handle: {
                            header: {
                                title: "Invoice Detail",
                                description: "View complete invoice information.",
                            },
                        },
                    },
                    {
                        path: ":id/edit",
                        element: <EditInvoicePage />,
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
                        element: <VendorsPage />,
                        handle: {
                            header: {
                                title: "Vendors",
                                description: "Manage your suppliers and expenses.",
                            },
                        },
                    },
                    {
                        path: ":id",
                        element: <VendorDetailPage />,
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
                        element: <ExpensesPage />,
                        handle: {
                            header: {
                                title: "Expenses",
                                description: "Manage your expenses.",
                            },
                        },
                    },
                    {
                        path: "new",
                        element: <AddExpensePage />,
                        handle: {
                            header: {
                                title: "Create New Expense",
                                description: "Effortlessly add your expenses here.",
                            },
                        },
                    },
                    {
                        path: ":id",
                        element: <ExpensesDetailPage />,
                        handle: {
                            header: {
                                title: "Expense Detail",
                                description: "View complete expense information.",
                            },
                        },
                    },
                    {
                        path: ":id/edit",
                        element: <EditExpensePage />,
                        handle: {
                            header: {
                                title: "Edit Expense",
                                description: "Update expense details.",
                            },
                        },
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