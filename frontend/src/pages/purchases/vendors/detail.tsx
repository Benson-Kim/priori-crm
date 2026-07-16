import ExcelSvg from "@/assets/icons/excel-document-svgrepo-com.svg?react";
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
import { formatCurrency, formatDate, getNameInitials, saveBlob } from "@/lib/utils";
import {
  downloadVendorStatementPdf,
  exportVendorCardExcel,
  exportVendorCardPdf,
  exportVendorStatementExcel,
  getVendor,
  getVendorCard,
  getVendorPayables,
  getVendorStatement,
  getVendorTransactions,
  type Vendor,
  type VendorCardKey,
  type VendorCardSummary,
  type VendorPayablesSummary,
  type VendorStatement
} from "@/services/vendorApi";
import { ChevronLeft, ChevronRight, Download, Eye, FileClock, Pencil } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";


/**
 * VendorSummaryCard — one of the three cards on the vendor detail Overview tab
 * (Total POs / Total Payments / Total Bills). Each card is self-contained: it
 * owns its date filter, fetches its own aggregate + paginated row list, tags
 * every row paid/pending, and exports to Excel / PDF.
 *
 * The backend card endpoints take explicit period_start/period_end (the same
 * contract as the vendor statement), so a preset is resolved to concrete dates
 * here before the request. The reusable PeriodRangePicker still drives the UX.
 */
import { PeriodRangePicker } from "@/components/ui/PeriodRangePicker";
import { isPeriodReady, resolvePeriod, type PeriodFilter } from "@/services/statementsApi";

// Source filter values shown in the type Select.
type VendorTxnType = "purchase_order" | "expense" | "payments";

// Unified row shape for the transaction table. Populated either from the
// combined transactions endpoint (POs / expenses — carries balance/status) or,
// for payments, from the payments card endpoint (carries invoice_number /
// payment_ref / parent_id). Only the columns for the active type are rendered.
interface VendorTxnRow {
  id: string;
  ref_no: string;
  date: string;
  amount: string | number;
  balance?: string | number;
  status?: string;
  days_overdue?: number;
  transaction_type?: string;
  invoice_number?: string | null;
  payment_ref?: string | null;
  parent_id?: string | null;
  source?: string;
}

// Which export card each source type maps to.
const CARD_KEY_BY_TYPE: Record<VendorTxnType, VendorCardKey> = {
  purchase_order: "purchase-orders",
  expense: "bills",
  payments: "payments",
};

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
  const [isExporting, setIsExporting] = useState(false);


  // Transaction table state
  const [activeTab, setActiveTab] = useState("all");
  // Source filter: purchase_order | expense | payments
  const [typeTab, setTypeTab] = useState<VendorTxnType>("purchase_order");
  const [currentPage, setCurrentPage] = useState(1);
  const [perPage, setPerPage] = useState(10);
  const [transactions, setTransactions] = useState<VendorTxnRow[]>([]);
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
      if (typeTab === "payments") {
        // Payments come from the dedicated card endpoint (PO + expense
        // payments unioned). The paid/pending status filter doesn't apply.
        const data = await getVendorCard(id, "payments", {
          page: currentPage,
          per_page: perPage,
        });
        setTransactions(
          data.items.map((it) => ({
            id: it.id,
            ref_no: it.ref_no,
            date: it.date,
            amount: it.amount,
            invoice_number: it.invoice_number,
            payment_ref: it.payment_ref,
            parent_id: it.parent_id,
            source: it.source,
          }))
        );
        setTotalPages(data.total_pages);
        return;
      }

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
        type: typeTab,
      });
      setTransactions(data.items as VendorTxnRow[]);
      setTotalPages(data.total_pages);
    } catch (err) {
      console.error("[VendorDetailPage] Failed to fetch transactions:", err);
    } finally {
      setIsLoadingTransactions(false);
    }
  }, [id, currentPage, perPage, activeTab, typeTab]);

  const getStatementPeriod = useCallback(
    (months?: number) => {
      const periodEnd = new Date().toISOString().split("T")[0];

      const periodStart = new Date();
      periodStart.setMonth(periodStart.getMonth() - (months ?? periodMonths));

      return {
        periodStart: periodStart.toISOString().split("T")[0],
        periodEnd,
      };
    },
    [periodMonths]
  );


  const fetchStatement = useCallback(
    async (months?: number) => {
      if (!id) return;

      setIsLoadingStatement(true);
      setStatementError(null);

      try {
        const { periodStart, periodEnd } = getStatementPeriod(months);
        const data = await getVendorStatement(id, periodStart, periodEnd);
        setStatement(data);
      } catch (err) {
        setStatementError(
          err instanceof Error ? err.message : "Failed to fetch statement"
        );
      } finally {
        setIsLoadingStatement(false);
      }
    },
    [id, getStatementPeriod]
  );

  /** Download this customer's statement as a PDF. */
  const handleDownloadPDFVendorStatement = useCallback(async () => {
    if (!id) return;

    try {
      const { periodStart, periodEnd } = getStatementPeriod();
      const blob = await downloadVendorStatementPdf(id, periodStart, periodEnd);
      saveBlob(blob, `Vendor_Statement_${periodStart}_to_${periodEnd}.pdf`);
    } catch (err) {
      setStatementError(
        err instanceof Error ? err.message : "Failed to download vendor statement PDF"
      );
    }
  }, [id, getStatementPeriod]);

  /** Export this customer's statement to an Excel workbook. */
  const handleExportExcelVendorStatement = useCallback(async () => {
    if (!id) return;

    try {
      const { periodStart, periodEnd } = getStatementPeriod();
      const blob = await exportVendorStatementExcel(id, periodStart, periodEnd);
      saveBlob(blob, `Vendor_Statement_${periodStart}_to_${periodEnd}.xlsx`);
    } catch (err) {
      setStatementError(err instanceof Error ? err.message : "Failed to export vendor statements excel");
    }
  }, [id, getStatementPeriod]);



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

  const actions: DropdownItem[] = [];

  if (mainTab === "overview") {
    actions.push({
      key: "edit",
      label: "Edit",
      icon: <Pencil size={16} />,
      onClick: () => setIsEditModalOpen(true),
    });

  } else {
    // Balance-aware statement PDF (payments applied + running balance).
    actions.push({
      key: "download-pdf-vendor-statement",
      label: "Download statement",
      icon: <FileClock size={16} />,
      onClick: handleDownloadPDFVendorStatement,
    });
    // Export this payments to Excel.
    actions.push({
      key: "export-excel-vendor-statement",
      label: "Export excel",
      icon: <ExcelSvg className="w-4 h-4" />,
      onClick: handleExportExcelVendorStatement,
    });
  }

  const getStatusBadge = (item: VendorTxnRow) => {
    if (!item.status) return null;
    if (item.status === "overdue" && (item.days_overdue ?? 0) > 0) {
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

  // Route a transaction row to its source document's View. For a payment we
  // route to the parent document (PO / expense) it was recorded against.
  const handleViewTransaction = (item: VendorTxnRow) => {
    if (item.source === "po_payment" && item.parent_id) {
      navigate(`/purchase-orders/${item.parent_id}`);
    } else if (item.source === "expense_payment" && item.parent_id) {
      navigate(`/expenses/${item.parent_id}`);
    } else if (item.transaction_type === "purchase_order") {
      navigate(`/purchase-orders/${item.id}`);
    } else if (item.transaction_type === "expense") {
      navigate(`/expenses/${item.id}`);
    }
  };


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

  // Source filter: isolate Expenses or Purchase Orders.
  const TYPE_TABS = [
    { value: "purchase_order", label: "Purchase Orders" },
    { value: "expense", label: "Expenses" },
    { value: "payments", label: "Payments" },
  ];

  // Reusable column definitions; each type renders only the ones it needs.
  const numberCol = {
    key: "number",
    header: "#",
    className: "w-[60px]",
    render: (_item: VendorTxnRow, index: number) => (
      <span className="text-gray-600">
        {(currentPage - 1) * perPage + index + 1}.
      </span>
    ),
  };
  const dateCol = {
    key: "date",
    header: "Date",
    render: (item: VendorTxnRow) => (
      <span className="text-gray-800 font-medium">{formatDate(item.date)}</span>
    ),
  };
  const refCol = (header: string) => ({
    key: "ref_no",
    header,
    render: (item: VendorTxnRow) => (
      <span
        className="text-gray-500 max-w-37.5 truncate block"
        title={item.ref_no}
      >
        {item.ref_no}
      </span>
    ),
  });

  const amountCol = {
    key: "amount",
    header: "Amount",
    render: (item: VendorTxnRow) => (
      <span className="text-gray-600">
        {formatCurrency(Number(item.amount), vendor.currency)}
      </span>
    ),
  };
  const balanceCol = {
    key: "balance",
    header: "Balance",
    render: (item: VendorTxnRow) => (
      <span className="text-gray-600">
        {formatCurrency(Number(item.balance ?? 0), vendor.currency)}
      </span>
    ),
  };
  const statusCol = {
    key: "status",
    header: "Status",
    render: (item: VendorTxnRow) => getStatusBadge(item),
  };
  const invoiceNumberCol = {
    key: "invoice_number",
    header: "Invoice Number",
    render: (item: VendorTxnRow) => (
      <span className="text-gray-600">{item.invoice_number || "—"}</span>
    ),
  };
  const paymentRefCol = {
    key: "payment_ref",
    header: "Payment Ref",
    render: (item: VendorTxnRow) => (
      <span className="text-gray-600">{item.payment_ref || "—"}</span>
    ),
  };
  const actionsCol = {
    key: "actions",
    header: "Actions",
    className: "w-[120px]",
    render: (item: VendorTxnRow) => (
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
  };

  // Type-specific columns (index + actions always present):
  //  - Purchase Orders : Date, PO Ref, Amount, Balance, Status
  //  - Expenses        : Date, Ref No, Amount, Status
  //  - Payments        : Date, Invoice Number, Payment Ref, Amount
  const columnsByType = {
    purchase_order: [
      numberCol,
      dateCol,
      refCol("PO Ref"),
      amountCol,
      balanceCol,
      statusCol,
      actionsCol,
    ],
    expense: [numberCol, dateCol, refCol("Ref No"), amountCol, statusCol, actionsCol],
    payments: [
      numberCol,
      dateCol,
      invoiceNumberCol,
      paymentRefCol,
      amountCol,
      actionsCol,
    ],
  };
  const columns = columnsByType[typeTab];

  // Export the currently-filtered type via the matching card export endpoint.
  const handleExport = async (kind: "excel" | "pdf") => {
    if (!id) return;
    setIsExporting(true);
    try {
      const card = CARD_KEY_BY_TYPE[typeTab];
      const stamp = new Date().toISOString().split("T")[0];
      const label = TYPE_TABS.find((t) => t.value === typeTab)?.label ?? "Vendor";
      const stem = `${displayName.replace(/\s+/g, "")}_${label.replace(/\s+/g, "")}_${stamp}`;
      if (kind === "excel") {
        const blob = await exportVendorCardExcel(id, card);
        saveBlob(blob, `${stem}.xlsx`);
      } else {
        const blob = await exportVendorCardPdf(id, card);
        saveBlob(blob, `${stem}.pdf`);
      }
    } catch (err) {
      console.error("[VendorDetailPage] Export failed:", err);
    } finally {
      setIsExporting(false);
    }
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
            Statement
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
            </>
          )}
          <Dropdown
            items={actions}
            className="flex items-center gap-2 px-4 py-3 border border-priori-purple text-priori-purple rounded-lg font-sans cursor-pointer hover:bg-purple-50 transition-colors"
          />
        </div>
      </div>

      {mainTab === "overview" && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-10 gap-6">
            <div className="rounded-2xl border border-gray-200 bg-white space-y-6 lg:col-span-3">
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

            <div className="lg:col-span-7 space-y-6">
              <div className="grid lg:grid-cols-6 gap-6">
                <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3 lg:col-span-3">
                  <p className="text-gray-500 text-lg py-3 text-center">Total Unpaid</p>
                  <p className="font-bold text-gray-800 text-2xl text-center">
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
                <Card className="rounded-2xl border border-gray-200 bg-white px-6 py-3 lg:col-span-3">
                  <p className="text-gray-500 text-lg py-3 text-center">Overdue</p>
                  <p className="font-bold text-gray-800 text-2xl text-center">
                    {formatCurrency(
                      Number(payables?.overdue_total || vendor.overdue_total || 0),
                      vendor.currency
                    )}
                  </p>
                </Card>
              </div>

              <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
                <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
                  {typeTab !== "payments" && (
                    <FilterTabs
                      tabs={TABS}
                      activeTab={activeTab}
                      onTabChange={setActiveTab}
                      className="gap-4"
                    />
                  )}
                  <div className="flex items-center gap-6">
                    <Dropdown
                      disabled={isExporting}
                      trigger={
                        <span className="inline-flex items-center gap-1.5 text-base font-medium text-gray-900">
                          {isExporting ? "Exporting…" : "Export"}
                          <Download size={16} />
                        </span>
                      }
                      items={[
                        {
                          key: "excel",
                          label: "Export Excel",
                          onClick: () => void handleExport("excel"),
                        },
                        {
                          key: "pdf",
                          label: "Download PDF",
                          onClick: () => void handleExport("pdf"),
                        },
                      ]}
                      className="flex items-center gap-2 px-4 py-3 border border-gray-300 text-priori-purple rounded-lg font-sans cursor-pointer bg-gray-50 hover:bg-purple-50 transition-colors"
                    />
                    <Select
                      options={TYPE_TABS}
                      value={typeTab}
                      onChange={(e) => setTypeTab(e.target.value as VendorTxnType)}
                      wrapperClassName="px-4 py-3"
                    />
                  </div>
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
          {/* Summary cards: Total POs / Total Payments / Total Bills. Each
              owns its own date filter + Excel/PDF export and lists the
              underlying transactions tagged paid/pending. */}
          <div className="space-y-4">
            <h2 className="text-[20px] font-bold text-gray-800">Supplier Statements</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <VendorSummaryCard
                vendorId={vendor.id}
                card="purchase-orders"
                title="POs"
                currency={vendor.currency}
              />
              <VendorSummaryCard
                vendorId={vendor.id}
                card="payments"
                title="Payments"
                currency={vendor.currency}
              />
              <VendorSummaryCard
                vendorId={vendor.id}
                card="bills"
                title="Invoices"
                currency={vendor.currency}
              />

            </div>
          </div>
        </div>
      )
      }

      {
        mainTab === "statements" && (
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
        )
      }
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




interface VendorSummaryCardProps {
  vendorId: string;
  card: VendorCardKey;
  title: string;
  currency: string;
}

const PER_PAGE = 5;



function stateBadge(state: "paid" | "pending") {
  const variant: BadgeVariant = state === "pending" ? "pending" : "paid";
  return <Badge className="p-0" variant={variant}>{state === "paid" ? "Paid" : "Pending"}</Badge>;
}

export function VendorSummaryCard({
  vendorId,
  card,
  title,
  currency,
}: Readonly<VendorSummaryCardProps>) {
  // Default preset gives an immediate, useful window without a 12-month load.
  const [period, setPeriod] = useState<PeriodFilter>({ range: "this_year" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState<VendorCardSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const periodParams = useCallback(() => {
    const { start, end } = resolvePeriod(period);
    const params: { period_start?: string; period_end?: string } = {};
    if (start) params.period_start = start;
    if (end) params.period_end = end;
    return params;
  }, [period]);

  const fetchCard = useCallback(async () => {
    if (!isPeriodReady(period)) return;
    setIsLoading(true);
    setError(null);
    try {
      const result = await getVendorCard(vendorId, card, {
        ...periodParams(),
        page,
        per_page: PER_PAGE,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load card");
    } finally {
      setIsLoading(false);
    }
  }, [vendorId, card, page, period, periodParams]);

  useEffect(() => {
    void fetchCard();
  }, [fetchCard]);

  // Reset to page 1 whenever the filter changes.
  useEffect(() => {
    setPage(1);
  }, [period]);


  const total = data ? Number(data.total) : 0;
  const paid = data ? Number(data.paid_total) : 0;
  const pending = data ? Number(data.pending_total) : 0;

  return (
    <Card className="rounded-2xl border border-gray-200 bg-white p-4 flex flex-col gap-2">
      {/* Header: title + total */}
      <div className="flex items-center justify-between gap-2">
        <p className="text-left font-bold text-lg text-content-primary">{title}</p>

        {/* Date filter */}
        <PeriodRangePicker
          period={period}
          onPeriodChange={setPeriod}
          selectWrapperClassName="max-w-[160px] py-3 px-4"
        />
      </div>

      {/* Transaction list */}
      <div className="min-h-30">
        {isLoading ? (
          <LoadingState message="Loading…" className="h-28" />
        ) : error ? (
          <p className="text-sm text-red-500 py-4">{error}</p>
        ) : data && data.items.length > 0 ? (
          <div className="flex flex-col gap-3">
            <div className="">
              <p className="text-gray-500">Total</p>
              <p className="font-bold text-content-primary text-lg">
                {formatCurrency(
                  Number(total), currency)} </p>
            </div>
            <div className="w-full flex items-center">
              <div className="flex flex-col w-1/2">
                {stateBadge("paid")}
                <span className="text-sm text-gray-700">
                  {formatCurrency(Number(paid), currency)}
                </span>
              </div>
              <div className="flex flex-col w-1/2">
                {stateBadge("pending")}
                <span className="text-sm text-gray-700">
                  {formatCurrency(Number(pending), currency)}
                </span>
              </div>
            </div>

          </div>
        ) : (
          <p className="text-sm text-gray-400 py-6 text-center">
            No records in this period.
          </p>
        )}
      </div>

    </Card>
  );
}
