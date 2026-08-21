/**
 * Sales Report page -- /reports/sales
 *
 * Three tabs: Summary | Ledger | Aged Receivables
 *
 * Currency selector in header -- controls all three tabs.
 * Period picker in header -- controls Summary and Ledger tabs.
 * Aged Receivables tab is point-in-time (no period filter).
 *
 * No charts. MetricCards + sortable Tables + Pagination.
 */

import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { LoadingState } from "@/components/ui/LoadingState";
import { MetricCard } from "@/components/ui/MetricCard";
import { Pagination } from "@/components/ui/Pagination";
import { ReportPeriodPicker } from "@/components/ui/ReportPeriodPicker";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import { useAgedReport } from "@/hooks/useAgedReport";
import { useDebounce } from "@/hooks/useDebounce";
import { useReportingDate } from "@/hooks/useReportingDate";
import { useTableSort } from "@/hooks/useTableSort";
import { CURRENCY_OPTIONS, DEFAULT_CURRENCY } from "@/lib/constants";
import { buildReportPeriodParams, defaultReportPeriod, isReportPeriodReady, type ReportPeriodFilter } from "@/lib/reportUtils";
import { formatCurrency, formatDisplayDate } from "@/lib/utils";
import {
  AGING_BUCKET_LABELS,
  AGING_BUCKET_ORDER,
  exportSalesReport,
  getAgedReceivables,
  getSalesCounts,
  getSalesLedger,
  getSalesReport,
  type AgedReceivableRow,
  type AgedReceivablesSummaryResponse,
  type SalesLedgerEntry,
  type SalesReportSummaryResponse,
  type SalesStatusCounts,
} from "@/services/reportsApi";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

type ActiveTab = "summary" | "ledger" | "aged";

/** Map an AgingBuckets field key from AGING_BUCKET_ORDER to the Pydantic model key. */
function agedBucketField(key: string): string {
  return key === "current" ? "current" : `days_${key}`;
}

export default function SalesReportPage() {
  const navigate = useNavigate();
  const reportingDay = useReportingDate();

  const [period, setPeriod] = useState<ReportPeriodFilter>(() => defaultReportPeriod(reportingDay));
  const previousReportingDay = useRef(reportingDay);
  const [currency, setCurrency] = useState(DEFAULT_CURRENCY);
  const [activeTab, setActiveTab] = useState<ActiveTab>("summary");

  const [summary, setSummary] = useState<SalesReportSummaryResponse | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [ledger, setLedger] = useState<SalesLedgerEntry[]>([]);
  const [counts, setCounts] = useState<SalesStatusCounts | null>(null);
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [ledgerTotal, setLedgerTotal] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(10);
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const {
    data: agedData,
    isLoading: agedLoading,
    error: agedError,
    setCurrency: setAgedCurrency,
  } = useAgedReport<AgedReceivablesSummaryResponse>({
    fetcher: getAgedReceivables,
    defaultCurrency: currency,
  });

  // Keep aged currency in sync with the page-level currency
  useEffect(() => {
    setAgedCurrency(currency);
  }, [currency, setAgedCurrency]);

  useEffect(() => {
    const previousDefault = defaultReportPeriod(previousReportingDay.current);
    setPeriod((current) =>
      JSON.stringify(current) === JSON.stringify(previousDefault)
        ? defaultReportPeriod(reportingDay)
        : current
    );
    previousReportingDay.current = reportingDay;
  }, [reportingDay]);

  const periodKey = JSON.stringify(buildReportPeriodParams(period, currency, reportingDay));

  // Fetch Summary
  useEffect(() => {
    if (!isReportPeriodReady(period, reportingDay)) return;
    let cancelled = false;
    setSummaryLoading(true);
    setSummaryError(null);
    getSalesReport(period, currency, reportingDay)
      .then((res) => { if (!cancelled) setSummary(res); })
      .catch((err: unknown) => {
        if (!cancelled)
          setSummaryError(err instanceof Error ? err.message : "Failed to load summary");
      })
      .finally(() => { if (!cancelled) setSummaryLoading(false); });
    return () => { cancelled = true; };
  }, [periodKey]);

  // Fetch Ledger + Counts
  useEffect(() => {
    if (!isReportPeriodReady(period, reportingDay)) return;
    let cancelled = false;
    setLedgerLoading(true);
    setLedgerError(null);

    Promise.all([
      getSalesLedger(period, currency, {
        status: statusFilter === "all" ? undefined : statusFilter,
        search: debouncedSearch || undefined,
        page,
        perPage,
        withTotal: true,
      }, reportingDay),
      getSalesCounts(period, currency, {}, reportingDay),
    ])
      .then(([ledgerRes, countsRes]) => {
        if (!cancelled) {
          setLedger(ledgerRes.items);
          setLedgerTotal(ledgerRes.metadata.total ?? null);
          setCounts(countsRes);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setLedgerError(err instanceof Error ? err.message : "Failed to load ledger");
      })
      .finally(() => { if (!cancelled) setLedgerLoading(false); });

    return () => { cancelled = true; };
  }, [periodKey, statusFilter, debouncedSearch, page, perPage]);

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [periodKey, statusFilter, debouncedSearch, perPage]);

  const { sortedData: sortedLedger, sortKey, sortDirection, handleSort } = useTableSort(ledger);

  const fmt = useCallback(
    (v: string | number | undefined) => formatCurrency(Number(v ?? 0), currency),
    [currency]
  );

  const handleExport = async () => {
    setLedgerError(null);
    if (!isReportPeriodReady(period, reportingDay)) return;
    setIsExporting(true);
    setExportError(null);
    try {
      await exportSalesReport(period, currency, reportingDay);
    } catch (err: unknown) {
      setExportError(
        err instanceof Error ? err.message : "Failed to export sales report"
      );
    } finally {
      setIsExporting(false);
    }
  };

  const ledgerTabs = useMemo(() => [
    { key: "all", label: "All", count: counts?.all },
    { key: "sent", label: "Sent", count: counts?.sent },
    { key: "partial", label: "Partial", count: counts?.partial },
    { key: "paid", label: "Paid", count: counts?.paid },
    { key: "overdue", label: "Overdue", count: counts?.overdue },
  ], [counts]);

  const agedCustomers = agedData?.customers ?? [];
  const {
    sortedData: sortedAged,
    sortKey: agedSortKey,
    sortDirection: agedSortDir,
    handleSort: agedHandleSort,
  } = useTableSort(agedCustomers);

  const pageTabs = [
    { key: "summary", label: "Summary" },
    { key: "ledger", label: "Ledger" },
    { key: "aged", label: "Aged Receivables" },
  ];

  const agedTotals = agedData?.totals;

  return (
    <div className="flex flex-col space-y-6">
      {/* Header controls card */}
      <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col xl:flex-row xl:justify-between gap-4">
        {/* Page tabs */}
        <FilterTabs
          tabs={pageTabs}
          activeTab={activeTab}
          onTabChange={(k) => setActiveTab(k as ActiveTab)}
        />
        <div className="flex flex-wrap items-center gap-2">
          <ReportPeriodPicker value={period} onChange={setPeriod} />
          <InlineSelect
            options={CURRENCY_OPTIONS}
            value={currency}
            onChange={setCurrency}
            aria-label="Currency"
            triggerClassName="px-4 py-3 rounded-2xl"
          />
        </div>
      </div>

      {exportError && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {exportError}
        </div>
      )}

      {/* Summary tab */}
      {activeTab === "summary" && (
        summaryLoading ? <LoadingState message="Loading sales summary..." className="h-64" /> :
          summaryError ? (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
              {summaryError}
            </div>
          ) :
            summary ? (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <MetricCard label="Net Revenue" value={fmt(summary.metrics.net_revenue)} change={null} />
                  <MetricCard label="Tax Collected" value={fmt(summary.metrics.tax_collected)} change={null} />
                  <MetricCard label="Total Invoiced" value={fmt(summary.metrics.total_invoiced)} change={null} />
                  <MetricCard label="Invoice Count" value={String(summary.metrics.invoice_count)} change={null} />
                  <MetricCard label="Outstanding" value={fmt(summary.metrics.outstanding_balance)} change={null} />
                  <MetricCard label="Overdue" value={fmt(summary.metrics.overdue_balance)} change={null} />
                  <MetricCard label="Gross Subtotal" value={fmt(summary.metrics.subtotal)} change={null} />
                  <MetricCard label="Discounts" value={fmt(summary.metrics.discount_total)} change={null} />
                </div>

                <div className="grid gap-6 lg:grid-cols-2">
                  <div className="bg-white rounded-2xl border border-gray-200 p-4 overflow-hidden">
                    <h3 className="font-bold py-3 leading-6 text-lg text-gray-900">Top Customers by Revenue</h3>
                    <div className="overflow-x-auto rounded-b-lg border border-white">
                      <Table
                        columns={[
                          {
                            key: "customer_name",
                            header: "Customer",
                            render: (r) => (
                              <span
                                className="block max-w-[170px] truncate"
                                title={r.customer_name}
                              >
                                {r.customer_name}
                              </span>
                            ),
                          },
                          { key: "invoice_count", header: "Invoices", className: "text-right" },
                          { key: "amount", header: `Revenue (${currency})`, className: "text-right", render: (r) => fmt(r.amount) },
                        ]}
                        data={summary.revenue_by_customer}
                        rowKey={(r) => r.customer_id}
                        // Half-width card: the shared 600px floor would scroll here.
                        tableClassName="min-w-0"
                        emptyMessage="No data for this period."
                      />
                    </div>
                  </div>
                  <div className="bg-white rounded-2xl border border-gray-200 p-4 overflow-hidden">
                    <h3 className="font-bold py-3 leading-6 text-lg text-gray-900">Revenue by Category</h3>
                    <div className="overflow-x-auto rounded-b-lg border border-white">
                      <Table
                        columns={[
                          {
                            key: "category",
                            header: "Category",
                            render: (r) => (
                              <span className="block max-w-[170px] truncate" title={r.category}>
                                {r.category}
                              </span>
                            ),
                          },
                          { key: "document_count", header: "Invoices", className: "text-right" },
                          { key: "amount", header: `Revenue (${currency})`, className: "text-right", render: (r) => fmt(r.amount) },
                        ]}
                        data={summary.revenue_by_category}
                        rowKey={(r) => r.category}
                        tableClassName="min-w-0"
                        emptyMessage="No data for this period."
                      />
                    </div>
                  </div>
                </div>
              </div>
            ) : null
      )}

      {/* Ledger tab */}
      {activeTab === "ledger" && (
        <>
          {ledgerError && (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
              {ledgerError}
            </div>
          )}

          <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
            <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
              <FilterTabs
                tabs={ledgerTabs}
                activeTab={statusFilter}
                onTabChange={(k) => setStatusFilter(k)}
              />
              <div className="flex flex-col xl:flex-row items-center gap-4">
                <SearchInput
                  placeholder="Search customer, reference..."
                  value={search}
                  onSearchChange={setSearch}
                  className="w-full sm:w-70"
                />
                <Button variant="outline" onClick={handleExport} disabled={isExporting}>
                  {isExporting ? "Exporting..." : "Export Excel"}
                </Button>
              </div>
            </div>

            <div className="overflow-x-auto rounded-b-lg border border-white">
              {ledgerLoading ? (
                <LoadingState message="Loading ledger..." className="h-48" />
              ) : (
                <Table
                  columns={[
                    { key: "date", header: "Date", sortKey: "date", render: (r) => formatDisplayDate(r.date) },
                    { key: "reference", header: "Reference" },
                    { key: "customer_name", header: "Customer", sortKey: "customer_name" },
                    {
                      key: "status",
                      header: "Status",
                      render: (r) => {
                        return <Badge variant={r.status as BadgeVariant}>{r.status}</Badge>;
                      },
                    },
                    { key: "net_revenue", header: `Net Revenue (${currency})`, sortKey: "net_revenue", className: "text-right", render: (r) => fmt(r.net_revenue) },
                    { key: "tax", header: `Tax (${currency})`, sortKey: "tax", className: "text-right", render: (r) => fmt(r.tax) },
                    { key: "amount", header: `Total (${currency})`, sortKey: "amount", className: "text-right", render: (r) => fmt(r.amount) },
                    { key: "balance_due", header: `Balance (${currency})`, sortKey: "balance_due", className: "text-right", render: (r) => fmt(r.balance_due) },
                  ]}
                  data={sortedLedger}
                  rowKey={(r) => r.id}
                  onRowClick={(r) => navigate(`/invoices/${r.id}`)}
                  sortable
                  sortKey={sortKey ?? undefined}
                  sortDirection={sortDirection}
                  onSort={handleSort}
                  emptyMessage="No invoices for this period."
                />
              )}
            </div>
          </div>

          {ledgerTotal != null && (
            <div className="py-3 px-4 border border-gray-200 mt-auto bg-white rounded-2xl">
              <Pagination
                currentPage={page}
                totalPages={Math.max(1, Math.ceil(ledgerTotal / perPage))}
                perPage={perPage}
                onPageChange={setPage}
                onPerPageChange={(v) => { setPerPage(v); setPage(1); }}
              />
            </div>
          )}
        </>
      )}

      {/* Aged Receivables tab */}
      {activeTab === "aged" && (
        <div className="space-y-6">
          {agedData && (
            <p className="text-sm text-content-secondary">
              As of: <span className="font-medium text-gray-800">{formatDisplayDate(agedData.as_of_date)}</span>
            </p>
          )}

          {agedError && (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
              {agedError}
            </div>
          )}

          {agedLoading ? (
            <LoadingState message="Loading aged receivables..." className="h-48" />
          ) : agedData ? (
            <>
              <div className="grid gap-3 sm:grid-cols-5">
                {AGING_BUCKET_ORDER.map((key) => (
                  <MetricCard
                    key={key}
                    label={AGING_BUCKET_LABELS[key]}
                    value={fmt(
                      (agedTotals?.[agedBucketField(key) as keyof typeof agedTotals] as string) ?? "0"
                    )}
                    change={null}
                  />
                ))}
              </div>

              <div className="bg-white rounded-2xl border border-gray-200 p-4">
                <div className="overflow-x-auto rounded-b-lg border border-white">
                  <Table
                    columns={[
                      { key: "customer_name", header: "Customer", sortKey: "customer_name" },
                      { key: "current", header: "Current", sortKey: "current", className: "text-right", render: (r) => fmt((r as AgedReceivableRow).current) },
                      { key: "days_1_30", header: "1-30 days", sortKey: "days_1_30", className: "text-right", render: (r) => fmt((r as AgedReceivableRow).days_1_30) },
                      { key: "days_31_60", header: "31-60 days", sortKey: "days_31_60", className: "text-right", render: (r) => fmt((r as AgedReceivableRow).days_31_60) },
                      { key: "days_61_90", header: "61-90 days", sortKey: "days_61_90", className: "text-right", render: (r) => fmt((r as AgedReceivableRow).days_61_90) },
                      { key: "days_90_plus", header: "90+ days", sortKey: "days_90_plus", className: "text-right", render: (r) => fmt((r as AgedReceivableRow).days_90_plus) },
                      { key: "total", header: `Total (${currency})`, sortKey: "total", className: "text-right font-semibold", render: (r) => fmt((r as AgedReceivableRow).total) },
                    ]}
                    data={sortedAged as unknown as AgedReceivableRow[]}
                    rowKey={(r) => (r as AgedReceivableRow).customer_id}
                    sortable
                    sortKey={agedSortKey ?? undefined}
                    sortDirection={agedSortDir}
                    onSort={agedHandleSort}
                    emptyMessage="No outstanding receivables."
                  />
                </div>
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
