import { DocumentOwnerHeader } from "@/components/documents/DocumentOwnerHeader";
import { VendorModal } from "@/components/modals/VendorModal";
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Divider } from "@/components/ui/Divider";
import { Dropdown, type DropdownItem } from "@/components/ui/Dropdown";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import { formatCurrency, formatDate, getNameInitials } from "@/lib/utils";
import {
  getVendor,
  getVendorPayables,
  getVendorStatement,
  getVendorTransactions,
  type Vendor,
  type VendorPayablesSummary,
  type VendorStatement,
  type VendorTransactionSummary,
} from "@/services/vendorApi";
import { ChevronLeft, ChevronRight, Eye, Pencil, Printer } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

export default function VendorDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [vendor, setVendor] = useState<Vendor | null>(null);
  const [payables, setPayables] = useState<VendorPayablesSummary | null>(null);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const label = "statement of accounts";
  const [mainTab, setMainTab] = useState<"overview" | "statements">("overview");

  // Statement state
  const [statement, setStatement] = useState<VendorStatement | null>(null);
  const [isLoadingStatement, setIsLoadingStatement] = useState(false);
  const [statementError, setStatementError] = useState<string | null>(null);
  const [periodMonths, setPeriodMonths] = useState(12);

  // Transaction table state
  const [activeTab, setActiveTab] = useState("all");
  // Source filter: all | expense | purchase_order
  const [typeTab, setTypeTab] = useState("all");
  const [currentPage, setCurrentPage] = useState(1);
  const [perPage, setPerPage] = useState(10);
  const [transactions, setTransactions] = useState<VendorTransactionSummary[]>(
    []
  );
  const [totalPages, setTotalPages] = useState(1);
  const [isLoadingTransactions, setIsLoadingTransactions] = useState(true);

  const [isEditModalOpen, setIsEditModalOpen] = useState(false);

  const fetchVendorData = useCallback(async () => {
    if (!id) return;
    try {
      setIsFetching(true);
      const vendorData = await getVendor(id);
      setVendor(vendorData);

      try {
        const payablesData = await getVendorPayables(id);
        setPayables(payablesData);
      } catch (err) {
        console.error("Failed to fetch payables", err);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to fetch vendor details"
      );
    } finally {
      setIsFetching(false);
    }
  }, [id]);

  const fetchTransactions = useCallback(async () => {
    if (!id) return;
    setIsLoadingTransactions(true);
    try {
      const statusMap: Record<string, string | undefined> = {
        all: undefined,
        pending: "pending",
        paid: "paid",
        overdue: "overdue",
      };
      const data = await getVendorTransactions(id, {
        page: currentPage,
        per_page: perPage,
        status: statusMap[activeTab],
        type: typeTab !== "all" ? typeTab : undefined,
      });
      setTransactions(data.items);
      setTotalPages(data.total_pages);
    } catch (err) {
      console.error("[VendorDetailPage] Failed to fetch transactions:", err);
    } finally {
      setIsLoadingTransactions(false);
    }
  }, [id, currentPage, perPage, activeTab, typeTab]);

  const fetchStatement = useCallback(
    async (months?: number) => {
      if (!id) return;
      setIsLoadingStatement(true);
      setStatementError(null);
      try {
        const periodEnd = new Date().toISOString().split("T")[0];
        const periodStart = new Date();
        const monthsToUse = months ?? periodMonths;
        periodStart.setMonth(periodStart.getMonth() - monthsToUse);
        const periodStartStr = periodStart.toISOString().split("T")[0];

        const data = await getVendorStatement(id, periodStartStr, periodEnd);
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
    void (async () => { await fetchVendorData(); })();
  }, [fetchVendorData]);

  useEffect(() => {
    if (mainTab === "statements") {
      void (async () => { await fetchStatement(); })();
    }
  }, [mainTab, fetchStatement, periodMonths]);

  useEffect(() => {
    void (async () => { await fetchTransactions(); })();
  }, [fetchTransactions]);

  useEffect(() => {
    startTransition(() => { setCurrentPage(1); });
  }, [activeTab, typeTab]);

  const actions: DropdownItem[] = [
    {
      key: "edit",
      label: "Edit",
      icon: <Pencil size={16} />,
      onClick: () => setIsEditModalOpen(true),
    },
  ];

  const getStatusBadge = (item: VendorTransactionSummary) => {
    if (item.status === "overdue" && item.days_overdue > 0) {
      return (
        <Badge variant="overdue">Overdue ({item.days_overdue} days)</Badge>
      );
    }
    const status = item.status.toLowerCase();
    // Covers both the payable (expense) statuses and the PO lifecycle
    // (draft / sent / billed / canceled) — all backed by shared Badge variants.
    const labelMap: Record<string, string> = {
      paid: "Paid",
      pending: "Pending",
      draft: "Draft",
      sent: "Sent",
      billed: "Billed",
      canceled: "Canceled",
    };
    const variant = (status === "pending" ? "draft" : status) as BadgeVariant;
    return (
      <Badge variant={variant}>
        {labelMap[status] ?? item.status}
      </Badge>
    );
  };

  // Route a transaction row to its source document's View.
  const handleViewTransaction = (item: VendorTransactionSummary) => {
    if (item.transaction_type === "purchase_order") {
      navigate(`/purchase-orders/${item.id}`);
    } else {
      navigate(`/expenses/${item.id}`);
    }
  };

  if (isFetching) {
    return (
      <LoadingState message="Loading vendor details..." className="h-64" />
    );
  }

  if (error || !vendor) {
    return (
      <div className="p-8 text-center">
        <p className="text-red-500 mb-4">{error || "Vendor not found"}</p>
        <Button variant="primary" onClick={() => navigate("/vendors")}>
          Back to Vendors
        </Button>
      </div>
    );
  }

  const displayName = vendor.vendor_name || vendor.email || "Vendor";
  const initials = vendor.display_initials || getNameInitials(displayName);
  const vendorSinceYear =
    vendor.vendor_since_year ||
    (vendor.created_at
      ? new Date(vendor.created_at).getFullYear()
      : new Date().getFullYear());

  const TABS = [
    { key: "all", label: "All" },
    { key: "pending", label: "Pending" },
    { key: "paid", label: "Paid" },
    { key: "overdue", label: "Overdue" },
  ];

  // Source filter (PO-13): isolate Expenses or Purchase Orders.
  const TYPE_TABS = [
    { value: "all", label: "All Types" },
    { value: "expense", label: "Expenses" },
    { value: "purchase_order", label: "Purchase Orders" },
  ];


  const columns = [
    {
      key: "number",
      header: "#",
      render: (_item: VendorTransactionSummary, index: number) => (
        <span className="text-gray-600">
          {(currentPage - 1) * perPage + index + 1}.
        </span>
      ),
      className: "w-[60px]",
    },
    {
      key: "ref_no",
      header: "Reference No.",
      render: (item: VendorTransactionSummary) => (
        <span
          className="text-gray-500 max-w-37.5 truncate block"
          title={item.ref_no}
        >
          {item.ref_no}
        </span>
      ),
    },
    {
      key: "date",
      header: "Date",
      render: (item: VendorTransactionSummary) => (
        <span className="text-gray-800 font-medium">
          {formatDate(item.date)}
        </span>
      ),
    },
    {
      key: "amount",
      header: "Amount",
      render: (item: VendorTransactionSummary) => (
        <span className="text-gray-600">
          {formatCurrency(Number(item.amount), vendor.currency)}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (item: VendorTransactionSummary) => getStatusBadge(item),
    },
    {
      key: "actions",
      header: "Actions",
      className: "w-[120px]",
      render: (item: VendorTransactionSummary) => (
        <Dropdown
          items={[
            {
              key: "view",
              label: "View",
              icon: <Eye size={16} />,
              onClick: () => handleViewTransaction(item),
            },
          ]}
        />
      ),
    },
  ];

  const handlePrintStatement = () => {
    window.print();
  };

  const handlePeriodChange = (direction: "prev" | "next") => {
    setPeriodMonths((prev) => {
      if (direction === "prev") {
        return Math.max(3, prev - 3);
      } else {
        return prev + 3;
      }
    });
  };

  return (
    <div className="space-y-6">
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

      {mainTab === "overview" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="rounded-2xl border border-gray-200 bg-white space-y-6">
            <div className="flex items-start gap-3 px-4 py-3 border-b border-gray-100">
              <p className="flex items-center justify-center bg-purple-25 w-12 h-12 rounded-full text-priori-purple font-bold text-[14px]">
                {initials}
              </p>
              <div className="flex-1">
                <p className="font-bold text-gray-800 text-[18px]">
                  {displayName}
                </p>
                <p className="text-[14px] text-gray-400">
                  Vendor Since {vendorSinceYear}
                </p>
              </div>
            </div>

            {vendor.email && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Email</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {vendor.email}
                </p>
              </div>
            )}
            {vendor.phone_primary && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Phone</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {vendor.phone_primary}
                </p>
              </div>
            )}
            {vendor.currency && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Currency</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {vendor.currency}
                </p>
              </div>
            )}
            {vendor.vat_number && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Tax ID/Pin Number</p>
                <p className="text-gray-800 font-bold text-[16px]">
                  {vendor.vat_number}
                </p>
              </div>
            )}
            {vendor.website && (
              <div className="p-4">
                <p className="text-gray-500 text-[14px]">Website</p>
                <a
                  href={vendor.website}
                  target="_blank"
                  rel="noreferrer"
                  className="text-priori-purple hover:underline font-bold text-[16px]"
                >
                  {vendor.website}
                </a>
              </div>
            )}
          </div>

          <div className="lg:col-span-2 space-y-4">
            <div className="grid grid-cols-2 gap-6">
              <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                <p className="text-gray-500 text-lg py-3">Total Unpaid</p>
                <p className="font-bold text-gray-800 text-2xl">
                  {formatCurrency(
                    Number(
                      payables?.total_unpaid ||
                      vendor.total_unpaid ||
                      vendor.payables ||
                      0
                    ),
                    vendor.currency
                  )}
                </p>
              </Card>
              <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3">
                <p className="text-gray-500 text-lg py-3">Overdue</p>
                <p className="font-bold text-gray-800 text-2xl">
                  {formatCurrency(
                    Number(payables?.overdue_total || vendor.overdue_total || 0),
                    vendor.currency
                  )}
                </p>
              </Card>
            </div>

            <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
              <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
                <FilterTabs
                  tabs={TABS}
                  activeTab={activeTab}
                  onTabChange={setActiveTab}
                  className="gap-4"
                />
                <Select
                  options={TYPE_TABS}
                  onChange={(e) => setTypeTab(e.target.value)}
                />
                {/* <FilterTabs
                  tabs={TYPE_TABS}
                  activeTab={typeTab}
                  onTabChange={setTypeTab}
                  className="gap-4"
                /> */}
              </div>

              <div className="rounded-b-lg border border-white">
                {isLoadingTransactions ? (
                  <LoadingState message="Loading transactions..." />
                ) : (
                  <Table
                    columns={columns}
                    data={transactions}
                    rowKey={(item) => item.id}
                    emptyMessage="No transactions found for this vendor."
                  />
                )}
              </div>
            </div>

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
              <div className="p-6">
                {/* Owner identity (logo + company block) — single source of
                    truth, snapshot-backed on issued documents. */}
                <DocumentOwnerHeader />

                <div className="flex flex-col md:flex-row justify-between items-start py-6">
                  <div className="flex flex-col gap-1">
                    <p className="text-sm text-gray-500 mb-1">To</p>
                    <p className="text-[16px] font-bold text-gray-800">
                      {statement.vendor.vendor_name}
                    </p>
                    {statement.vendor && (
                      <>
                        <p className="text-sm text-gray-600">
                          {statement.vendor.phone_primary ?? statement.vendor.phone_secondary ?? "-"}
                        </p>
                        <p className="text-sm text-gray-600">
                          {statement.vendor.email}
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
                            Number(statement.summary.opening_balance),
                            statement.vendor.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                      <div className="flex justify-between items-center font-bold text-gray-800">
                        <span className="">Invoiced Amount</span>
                        <span className="font-normal">
                          {formatCurrency(
                            Number(statement.summary.invoiced_amount),
                            statement.vendor.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                      <div className="flex justify-between items-center font-bold text-gray-800">
                        <span className="">Amount Paid</span>
                        <span className="font-normal">
                          {formatCurrency(
                            Number(statement.summary.amount_paid),
                            statement.vendor.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                      <Divider />
                      <div className="flex justify-between items-center font-bold text-gray-800">
                        <span className="">Balance Due</span>
                        <span className="font-normal">
                          {formatCurrency(
                            Number(statement.summary.balance_due),
                            statement.vendor.currency ?? "Ksh"
                          )}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="p-6">
                <table className="w-full text-[16px] min-w-[800px] pb-3">
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
                          Number(statement.summary.opening_balance),
                          statement.vendor.currency
                        )}
                      </td>
                      <td className="px-3 py-4 text-center text-gray-800">-</td>
                      <td className="px-3 py-4 text-right text-gray-800">
                        {formatCurrency(
                          Number(statement.summary.opening_balance),
                          statement.vendor.currency
                        )}
                      </td>
                    </tr>

                    {statement.transactions.map((transaction, index) => (
                      <tr key={index} className="grid grid-cols-5 gap-4 pt-2">
                        <td className="col-span-2 px-3 py-4 flex flex-col gap-1">
                          <span className="font-bold text-gray-800">
                            {transaction.description.split("—")[0]?.trim()}
                          </span>
                          <span className="text-gray-600 text-sm whitespace-pre-wrap">
                            {transaction.description.includes("—")
                              ? transaction.description.split("—")[1]?.trim()
                              : formatDate(transaction.date)}
                          </span>
                        </td>
                        <td className="px-3 py-4 text-center text-gray-800">
                          {Number(transaction.amount) > 0
                            ? formatCurrency(
                              Number(transaction.amount),
                              statement.vendor.currency
                            )
                            : "-"}
                        </td>
                        <td className="px-3 py-4 text-center text-gray-800">
                          {Number(transaction.payment) > 0
                            ? formatCurrency(
                              Number(transaction.payment),
                              statement.vendor.currency
                            )
                            : "-"}
                        </td>
                        <td className="px-3 py-4 text-right text-gray-800">
                          {formatCurrency(
                            Number(transaction.balance),
                            statement.vendor.currency
                          )}
                        </td>
                      </tr>
                    ))}

                    <tr className="flex items-center justify-end border-t-2 border-purple-25">
                      <td className="px-3 py-4" colSpan={4}>
                        <span className="font-bold text-gray-900">
                          Balance Due
                        </span>
                      </td>
                      <td className="px-3 py-4 text-right font-bold text-gray-800">
                        {formatCurrency(
                          Number(statement.summary.balance_due),
                          statement.vendor.currency
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
      <VendorModal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        vendorId={id}
        onSuccess={() => {
          fetchVendorData();
          setIsEditModalOpen(false);
        }}
      />
    </div>
  );
}
