import { createBrowserRouter, Navigate } from "react-router-dom";

import CustomersPage from "@/pages/customers";
import AddCustomerPage from "@/pages/customers/add";
import EditCustomerPage from "@/pages/customers/edit";
import CustomerDetailsPage from "@/pages/customers/detail";
import InvoicesPage from "@/pages/invoices";
import AddInvoicePage from "@/pages/invoices/add";
import InvoiceDetailPage from "@/pages/invoices/detail";
import EditInvoicePage from "@/pages/invoices/edit";
import ProductsPage from "@/pages/products";
import QuotesPage from "@/pages/quotes";
import DefaultLayout from "./layout/default-layout";

const routes = [
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
                                description: "Detailed overview of customer information and history.",
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
                element: <QuotesPage />,
                handle: {
                    header: {
                        title: "Quotes",
                        description: "Effortlessly handle your billing and quotes here.",
                    },
                },
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
                path: "products",
                element: <ProductsPage />,
                handle: {
                    header: {
                        title: "Products",
                        description: "Manage your catalog of products and services.",
                    },
                },
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