import { DocumentOwnerHeader } from "@/components/documents/DocumentOwnerHeader";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Divider } from "@/components/ui/Divider";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { Table } from "@/components/ui/Table";
import type { CustomerStatement } from "@/lib/types";
import { useConfirm } from "@/hooks/useConfirm";
import { formatCurrency, formatDate, getNameInitials } from "@/lib/utils";
import {
  getCustomer,
  getCustomerStatement,
  type CustomerDetail,
} from "@/services/customerApi";
import {
  getInvoiceCounts,
  getInvoices,
  markAsSent,
  type InvoiceStatusCounts,
  type InvoiceSummary
} from "@/services/invoiceApi";
import {
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Eye,
  Printer
} from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

export default function CustomerDetailsPage() {
  const { id } = useParams<{ id: string }>();
  const { showConfirm, ConfirmDialog } = useConfirm();
  const [customer, setCustomer] = useState<CustomerDetail | null>(null);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const label = "statement of accounts";
  const [mainTab, setMainTab] = useState<"overview" | "statements">("overview");

  // Statement state
  const [statement, setStatement] = useState<CustomerStatement | null>(null);
  const [isLoadingStatement, setIsLoadingStatement] = useState(false);
  const [statementError, setStatementError] = useState<string | null>(null);
  const [periodMonths, setPeriodMonths] = useState(12);

  // Invoice table state
  const [activeTab, setActiveTab] = useState("all");
  const [currentPage, setCurrentPage] = useState(1);
  const [perPage, setPerPage] = useState(5);
  const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
  const [totalPages, setTotalPages] = useState(1);
  const [counts, setCounts] = useState<InvoiceStatusCounts>({
    all: 0,
    draft: 0,
    sent: 0,
    partial: 0,
    paid: 0,
    overdue: 0,
    canceled: 0,
  });
  const [isLoadingInvoices, setIsLoadingInvoices] = useState(true);

  const fetchCustomer = useCallback(async () => {
    if (!id) return;
    try {
      setIsFetching(true);
      const data = await getCustomer(id);
      setCustomer(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to fetch customer details"
      );
    } finally {
      setIsFetching(false);
    }
  }, [id]);

  const fetchInvoices = useCallback(async () => {
    if (!id) return;
    setIsLoadingInvoices(true);
    try {
      const statusMap: Record<string, string | undefined> = {
        all: undefined,
        pending: "draft",
        paid: "paid",
        overdue: "overdue",
      };
      const data = await getInvoices({
        page: currentPage,
        per_page: perPage,
        status: statusMap[activeTab],
        customerId: id,
      });
      setInvoices(data.items);
      setTotalPages(data.total_pages);
    } catch (err) {
      console.error("[CustomerDetailsPage] Failed to fetch invoices:", err);
    } finally {
      setIsLoadingInvoices(false);
    }
  }, [id, currentPage, perPage, activeTab]);

  const fetchCounts = useCallback(async () => {
    if (!id) return;
    try {
      const data = await getInvoiceCounts({ customerId: id });
      setCounts(data);
    } catch (err) {
      console.error("[CustomerDetailsPage] Failed to fetch counts:", err);
    }
  }, [id]);

  const fetchStatement = useCallback(
    async (months?: number) => {
      if (!id) return;
      setIsLoadingStatement(true);
      setStatementError(null);
      try {
        // Calculate date range based on months
        const periodEnd = new Date().toISOString().split("T")[0];
        const periodStart = new Date();
        const monthsToUse = months ?? periodMonths;
        periodStart.setMonth(periodStart.getMonth() - monthsToUse);
        const periodStartStr = periodStart.toISOString().split("T")[0];

        const data = await getCustomerStatement(id, periodStartStr, periodEnd);
        setStatement(data);
      } catch (err) {
        setStatementError(
          err instanceof Error ? err.message : "Failed to fetch statement"
        );
      } finally {
        setIsLoadingStatement(false);
      }
    },
    [id, periodMonths]
  );

  useEffect(() => {
    void (async () => { await fetchCustomer(); })();
  }, [fetchCustomer]);

  useEffect(() => {
    if (mainTab === "statements") {
      void (async () => { await fetchStatement(); })();
    }
  }, [mainTab, fetchStatement, periodMonths]);

  useEffect(() => {
    void (async () => { await fetchInvoices(); })();
  }, [fetchInvoices]);

  useEffect(() => {
    void (async () => { await fetchCounts(); })();
  }, [fetchCounts]);

  useEffect(() => {
    startTransition(() => { setCurrentPage(1); });
  }, [activeTab]);


  const actions: DropdownItem[] = [];

  const handleMarkAsSent = (invoice: InvoiceSummary) => {
      showConfirm({
          title: "Mark as sent?",
          description: "Are you sure you want to mark this invoice as sent?",
          confirmLabel: "Yes, mark as sent",
          onConfirm: async () => {
              try {
                  await markAsSent(invoice.id);
                  fetchInvoices();
                  fetchCounts();
              } catch (err) {
                  setError(err instanceof Error ? err.message : "Failed to mark invoice as sent");
              }
          },
      });
  };

  const getInvoiceActions = (invoice: InvoiceSummary): DropdownItem[] => {
      const rowActions: DropdownItem[] = [
          {
              key: "view",
              label: "View",
              icon: <Eye size={16} />,
              onClick: () => navigate(`/invoices/${invoice.id}`),
          },
      ];

      if (invoice.status === "draft") {
          rowActions.push({
              key: "mark-as-sent",
              label: "Mark as sent",
              icon: <CheckCircle size={16} />,
              onClick: () => handleMarkAsSent(invoice),
          });
      }

      return rowActions;
  };

  const getInvoiceStatusBadge = (item: InvoiceSummary) => {
    if (item.is_overdue && item.days_overdue > 0) {
      return (
        <Badge variant="overdue">Overdue ({item.days_overdue} days)</Badge>
      );
    }
    const status = item.status.toLowerCase() as
      | "paid"
      | "sent"
      | "draft"
      | "partial"
      | "canceled";

    const labelMap: Record<string, string> = {
      paid: "Paid",
      sent: "Sent",
      partial: "Partial",
      canceled: "Canceled",
      draft: "Draft",
    };

    return <Badge variant={status}>{labelMap[status] ?? item.status}</Badge>;
  };

  if (isFetching) {
    return (
      <LoadingState message="Loading customer details..." className="h-64" />
    );
  }

  if (error || !customer) {
    return (
      <div className="p-8 text-center">
        <p className="text-red-500 mb-4">{error || "Customer not found"}</p>
        <Button variant="primary" onClick={() => navigate("/customers")}>
          Back to Customers
        </Button>
      </div>
    );
  }

  const { customer: details, financial_summary } = customer;

  actions.push({
    key: "edit",
    label: "Edit",
    onClick: () => navigate(`/customers/${details.id}/edit`),
  });

  actions.push({
    key: "create-invoice",
    label: "Create Invoice",
    onClick: () => navigate(`/invoices/add?customerId=${details.id}`),
  });

  actions.push({
    key: "create-quote",
    label: "Create Quote",
    onClick: () => navigate(`/quotes/add?customerId=${details.id}`),
  });

  // Create display name from customer data
  const displayName =
    details.company_name ||
    `${details.first_name} ${details.last_name}`.trim() ||
    details.email;

  // Get customer initials from display_name
  const initials = getNameInitials(displayName);

  // Get year from created_at
  const customerSinceYear = details.created_at
    ? new Date(details.created_at).getFullYear()
    : new Date().getFullYear();

  const TABS = [
    { key: "all", label: "All", count: counts.all },
    { key: "pending", label: "Pending", count: counts.draft },
    { key: "paid", label: "Paid", count: counts.paid },
    { key: "overdue", label: "Overdue", count: counts.overdue },
  ];

  const columns = [
    {
      key: "number",
      header: "#",
      render: (_item: InvoiceSummary, index: number) => (
        <span className="text-gray-600">
          {(currentPage - 1) * perPage + index + 1}.
        </span>
      ),
      className: "w-[60px]",
    },
    {
      key: "invoice_number",
      header: "Invoice No.",
      render: (item: InvoiceSummary) => (
        <span
          className="text-gray-500 max-w-37.5 truncate block"
          title={item.invoice_number}
        >
          {item.invoice_number}
        </span>
      ),
    },
    {
      key: "transaction_date",
      header: "Date",
      render: (item: InvoiceSummary) => (
        <span className="text-gray-800 font-medium">
          {formatDate(item.transaction_date)}
        </span>
      ),
    },
    {
      key: "total_due",
      header: "Amount",
      render: (item: InvoiceSummary) => (
        <span className="text-gray-600">
          {formatCurrency(Number(item.total_due), item.currency)}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (item: InvoiceSummary) => getInvoiceStatusBadge(item),
    },
    {
      key: "actions",
      header: "Actions",
      className: "w-[120px]",
      render: (item: InvoiceSummary) => (
        <Dropdown items={getInvoiceActions(item)} />
      ),
    },
  ];

  const handlePrintStatement = () => {
    window.print();
  };

  const handlePeriodChange = (direction: "prev" | "next") => {
    setPeriodMonths((prev) => {
      if (direction === "prev") {
        return Math.max(3, prev - 3); // Don't go below 3 months
      } else {
        return prev + 3; // Increase by 3 months
      }
    });
  };

  return (
    <div className="space-y-6">
      {/* Header / Tab Navigation */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 border-b border-gray-200">
          <button
            onClick={() => setMainTab("overview")}
            className={`px-6 py-3 font-semibold transition-colors relative ${mainTab === "overview"
              ? "text-priori-purple"
              : "text-gray-500 hover:text-gray-700"
              }`}
          >
            Overview
            {mainTab === "overview" && (
              <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-priori-purple" />
            )}
          </button>
          <button
            onClick={() => setMainTab("statements")}
            className={`px-6 py-3 font-semibold transition-colors relative ${mainTab === "statements"
              ? "text-priori-purple"
              : "text-gray-500 hover:text-gray-700"
              }`}
          >
            Statements
            {mainTab === "statements" && (
              <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-priori-purple" />
            )}
          </button>
        </div>

        {/* Controls Container - conditionally renders based on active tab */}
        <div className="flex justify-end items-center gap-3">
          {mainTab === "statements" && statement && (
            <>
              <div className="flex items-center gap-1 border border-gray-300 rounded-lg px-4 py-3">
                <button
                  className="p-1 hover:bg-gray-100 rounded"
                  onClick={() => handlePeriodChange("prev")}
                >
                  <ChevronLeft size={18} className="text-gray-600" />
                </button>
                <span className="text-sm font-medium text-gray-700 px-2">
                  Last {periodMonths} Months
                </span>
                <button
                  className="p-1 hover:bg-gray-100 rounded"
                  onClick={() => handlePeriodChange("next")}
                >
                  <ChevronRight size={18} className="text-gray-600" />
                </button>
              </div>
              <Button
                variant="outline"
                onClick={handlePrintStatement}
                className="border-gray-300 text-gray-700"
              >
                <Printer size={16} className="mr-2" />
                Print Statement
              </Button>
            </>
          )}
          <Dropdown
            items={actions}
            className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
          />
        </div>
      </div>

      {/* Overview Tab Content */}
      {mainTab === "overview" && (
        <div className="grid grid-cols-1 lg:grid-cols-10 gap-6">
          {/* Left Column: Customer Info Card */}
          <div className="rounded-2xl border border-gray-200 bg-white space-y-6 lg:col-span-3">
            <div className="flex items-start gap-3 px-4 py-3  border-b border-gray-100">
              <p className="flex items-center justify-center bg-purple-25 w-12 h-12 rounded-full text-priori-purple font-bold text-[14px]">
                {initials}
              </p>
              <div className="flex-1">
                <p className="font-bold text-gray-800 text-[18px]">
                  {displayName}
                </p>
                <p className="text-[14px] text-gray-400">
                  Customer Since {customerSinceYear}
                </p>
              </div>
            </div>

            {/* Contact Information */}
            {details.email && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Email</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {details.email}
                </p>
              </div>
            )}
            {details.phone && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Phone</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {details.phone}
                </p>
              </div>
            )}
            {details.currency && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Currency</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {details.currency}
                </p>
              </div>
            )}
            {details.vat_number && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Tax ID/Pin Number</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {details.vat_number}
                </p>
              </div>
            )}
            {details.website && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Website</p>
                <a
                  href={details.website}
                  target="_blank"
                  rel="noreferrer"
                  className="text-priori-purple hover:underline font-bold text-[16px]"
                >
                  {details.website}
                </a>
              </div>
            )}
          </div>

          {/* Right Column: Stats Cards & Invoice Table */}
          <div className="lg:col-span-7 space-y-6">
            <div className="grid grid-cols-2 gap-6">
              <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                <p className="text-gray-500 text-lg py-3">Total Unpaid</p>
                <p className="font-bold text-gray-800 text-2xl">
                  {formatCurrency(
                    Number(financial_summary.total_unpaid),
                    details.currency ?? ""
                  )}
                </p>
              </Card>
              <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                <p className="text-gray-500 text-lg py-3">Overdue</p>
                <p className="font-bold text-gray-800 text-2xl">
                  {formatCurrency(
                    Number(financial_summary.overdue),
                    details.currency ?? ""
                  )}
                </p>
              </Card>
            </div>

            {/* Invoice Table */}
            <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
              <h3 className="text-[18px] font-bold text-gray-800">Invoices</h3>
              {/* Actions Bar */}
              <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
                <FilterTabs
                  tabs={TABS}
                  activeTab={activeTab}
                  onTabChange={setActiveTab}
                />
              </div>

              {/* Table Area */}
              <div className="rounded-b-lg border border-white">
                {isLoadingInvoices ? (
                  <LoadingState message="Loading invoices..." />
                ) : (
                  <Table
                    columns={columns}
                    data={invoices}
                    rowKey={(item) => item.id}
                    emptyMessage="No invoices found for this customer."
                  />
                )}
              </div>
            </div>

            {/* Pagination Footer */}
            <div className="py-3 px-4 border border-gray-200 bg-white rounded-2xl">
              <Pagination
                currentPage={currentPage}
                totalPages={totalPages}
                perPage={perPage}
                onPageChange={setCurrentPage}
                onPerPageChange={setPerPage}
              />
            </div>
          </div>
        </div>
      )}

      {/* Statements Tab Content */}
      {mainTab === "statements" && (
        <div className="bg-white rounded-[20px] border-2 border-purple-25 overflow-hidden">
          {isLoadingStatement ? (
            <LoadingState message="Loading statement..." className="h-64" />
          ) : statementError ? (
            <Card className="p-8 text-center">
              <p className="text-red-500 mb-4">{statementError}</p>
              <Button variant="primary" onClick={() => fetchStatement()}>
                Retry
              </Button>
            </Card>
          ) : statement ? (
            <>
              {/* Statement Header */}
              <div className="p-6">
                <DocumentOwnerHeader editable={false} />

                <div className="flex flex-col md:flex-row justify-between items-start py-6">
                  {/* Document To */}
                  <div className="flex flex-col gap-1">
                    <p className="text-sm text-gray-500 mb-1">To</p>
                    <p className="text-[16px] font-bold text-gray-800">
                      {statement.customer.company_name ||
                        `${statement.customer.first_name} ${statement.customer.last_name}`}
                    </p>
                    {statement.customer && (
                      <>
                        <p className="text-sm text-gray-600">
                          {statement.customer.phone}
                        </p>
                        <p className="text-sm text-gray-600">
                          {statement.customer.email}
                        </p>
                      </>
                    )}
                  </div>

                  <div className="flex flex-col gap-3">
                    <h2 className="text-[24px] font-black text-priori-purple tracking-wider leading-8 uppercase text-end">
                      {label}
                    </h2>
                    <div className="flex flex-col gap-1">
                      <p className="text-gray-800 self-end">
                        {formatDate(statement.period_start)}
                        <span className="font-bold"> To </span>
                        {formatDate(statement.period_end)}
                      </p>
                      <h3 className="bg-purple-25 py-2 text-gray-800 uppercase font-bold">
                        Account summary
                      </h3>
                      <div className="flex justify-between items-center font-bold text-gray-800">
                        <span className="">Opening Balance</span>
                        <span className="font-normal">
                          {formatCurrency(
                            statement.summary.opening_balance,
                            statement.customer.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                      <div className="flex justify-between items-center font-bold text-gray-800">
                        <span className="">Invoiced Amount</span>
                        <span className="font-normal">
                          {formatCurrency(
                            statement.summary.invoiced_amount,
                            statement.customer.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                      <div className="flex justify-between items-center font-bold text-gray-800">
                        <span className="">Amount Paid</span>
                        <span className="font-normal">
                          {formatCurrency(
                            statement.summary.amount_paid,
                            statement.customer.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                      <Divider />
                      <div className="flex justify-between items-center font-bold text-gray-800">
                        <span className="">Balance Due</span>
                        <span className="font-normal">
                          {formatCurrency(
                            statement.summary.balance_due,
                            statement.customer.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Transactions Table */}
              <div className="p-6">
                <table className="w-full text-[16px] min-w-200 pb-3">
                  <thead>
                    <tr className="bg-priori-purple text-white px-3 py-4 grid grid-cols-5 rounded-t-lg">
                      <th className="text-left px-3 font-bold leading-8 col-span-2">
                        Date
                      </th>
                      <th className="text-center px-3 font-bold leading-8">
                        Amount
                      </th>
                      <th className="text-right px-3 font-bold leading-8">
                        Payment
                      </th>
                      <th className="text-right px-3 font-bold leading-8">
                        Balance
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* Opening Balance Row */}
                    <tr className="grid grid-cols-5 gap-4 pt-2">
                      <td className="col-span-2 px-3 py-4 flex flex-col gap-1">
                        <span className="font-bold text-gray-800">
                          Opening Balance
                        </span>
                        <span className="text-gray-600 text-sm whitespace-pre-wrap">
                          -
                        </span>
                      </td>
                      <td className="px-3 py-4 text-center text-gray-800">
                        {formatCurrency(
                          statement.summary.opening_balance,
                          statement.customer.currency
                        )}
                      </td>
                      <td className="px-3 py-4 text-center text-gray-800">-</td>
                      <td className="px-3 py-4 text-right text-gray-800">
                        {formatCurrency(
                          statement.summary.opening_balance,
                          statement.customer.currency
                        )}
                      </td>
                    </tr>

                    {/* Transaction Rows */}
                    {statement.transactions.map((transaction, index) => (
                      <tr key={index} className="grid grid-cols-5 gap-4 pt-2">
                        <td className="col-span-2 px-3 py-4 flex flex-col gap-1">
                          <span className="font-bold text-gray-800">
                            {transaction.description.split("—")[0].trim()}
                          </span>
                          <span className="text-gray-600 text-sm whitespace-pre-wrap">
                            {transaction.description.includes("—")
                              ? transaction.description.split("—")[1].trim()
                              : formatDate(transaction.date)}
                          </span>
                        </td>
                        <td className="px-3 py-4 text-center text-gray-800">
                          {transaction.amount > 0
                            ? formatCurrency(
                              transaction.amount,
                              statement.customer.currency
                            )
                            : "-"}
                        </td>
                        <td className="px-3 py-4 text-center text-gray-800">
                          {transaction.payment > 0
                            ? formatCurrency(
                              transaction.payment,
                              statement.customer.currency
                            )
                            : "-"}
                        </td>
                        <td className="px-3 py-4 text-right text-gray-800">
                          {formatCurrency(
                            transaction.balance,
                            statement.customer.currency
                          )}
                        </td>
                      </tr>
                    ))}

                    {/* Balance Due Row */}
                    <tr className="flex items-center justify-end border-t-2 border-purple-25">
                      <td className="px-3 py-4" colSpan={4}>
                        <span className="font-bold text-gray-900">
                          Balance Due
                        </span>
                      </td>
                      <td className="px-3 py-4 text-right font-bold text-gray-800">
                        {formatCurrency(
                          statement.summary.balance_due,
                          statement.customer.currency
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <Card className="p-8 text-center">
              <p className="text-gray-500">No statement data available</p>
            </Card>
          )}
        </div>
      )}

      {ConfirmDialog}
    </div>
  );
}
