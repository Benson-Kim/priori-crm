/**
 * Purchases Report page -- /reports/purchases
 *
 * Three tabs: Summary | Ledger | Aged Payables
 *
 * Currency selector in header -- controls all three tabs.
 * Period picker in header -- controls Summary and Ledger tabs.
 * Aged Payables tab is point-in-time (no period filter).
 *
 * Ledger source filter: All / Expenses / Purchase Orders
 * Row click navigates to /expenses/:id or /purchase-orders/:id
 * based on source_type.
 *
 * Aged Payables note: POs appear in "Current" only (no due_date on PO model).
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
import { useTableSort } from "@/hooks/useTableSort";
import { CURRENCY_OPTIONS, DEFAULT_CURRENCY } from "@/lib/constants";
import { buildReportPeriodParams, defaultReportPeriod, isReportPeriodReady, type ReportPeriodFilter } from "@/lib/reportUtils";
import { formatCurrency, formatDisplayDate } from "@/lib/utils";
import {
  AGING_BUCKET_LABELS,
  AGING_BUCKET_ORDER,
  exportPurchasesReport,
  getAgedPayables,
  getPurchasesCounts,
  getPurchasesLedger,
  getPurchasesReport,
  type AgedPayableRow,
  type AgedPayablesSummaryResponse,
  type PurchasesLedgerEntry,
  type PurchasesReportSummaryResponse,
  type PurchasesSourceCounts,
} from "@/services/reportsApi";
import { useReportingDate } from "@/hooks/useReportingDate";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

type ActiveTab = "summary" | "ledger" | "aged";

function agedBucketField(key: string): string {
  return key === "current" ? "current" : `days_${key}`;
}

export default function PurchasesReportPage() {
  const navigate = useNavigate();
  const reportingDay = useReportingDate();

  const [period, setPeriod] = useState<ReportPeriodFilter>(() => defaultReportPeriod(reportingDay));
  const previousReportingDay = useRef(reportingDay);
  const [currency, setCurrency] = useState(DEFAULT_CURRENCY);
  const [activeTab, setActiveTab] = useState<ActiveTab>("summary");

  const [summary, setSummary] = useState<PurchasesReportSummaryResponse | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [ledger, setLedger] = useState<PurchasesLedgerEntry[]>([]);
  const [counts, setCounts] = useState<PurchasesSourceCounts | null>(null);
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [ledgerTotal, setLedgerTotal] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(10);
  const [sourceFilter, setSourceFilter] = useState<"all" | "expense" | "purchase_order">("all");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const {
    data: agedData,
    isLoading: agedLoading,
    error: agedError,
    setCurrency: setAgedCurrency,
  } = useAgedReport<AgedPayablesSummaryResponse>({
    fetcher: getAgedPayables,
    defaultCurrency: currency,
  });

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
    getPurchasesReport(period, currency, reportingDay)
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
      getPurchasesLedger(period, currency, {
        source: sourceFilter === "all" ? undefined : sourceFilter,
        search: debouncedSearch || undefined,
        page,
        perPage,
      }, reportingDay),
      getPurchasesCounts(period, currency, {
        source: sourceFilter === "all" ? undefined : sourceFilter,
        search: debouncedSearch || undefined,
      }, reportingDay),
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
  }, [periodKey, sourceFilter, debouncedSearch, page, perPage, reportingDay]);

  useEffect(() => {
    setPage(1);
  }, [periodKey, sourceFilter, debouncedSearch, perPage]);

  const { sortedData: sortedLedger, sortKey, sortDirection, handleSort } =
    useTableSort(ledger);

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
      await exportPurchasesReport(period, currency, reportingDay);
    } catch (err: unknown) {
      setExportError(
        err instanceof Error ? err.message : "Failed to export purchases report"
      );
    } finally {
      setIsExporting(false);
    }
  };

  const handleLedgerRowClick = useCallback(
    (r: PurchasesLedgerEntry) => {
      if (r.source_type === "expense") {
        navigate(`/expenses/${r.source_id}`);
      } else {
        navigate(`/purchase-orders/${r.source_id}`);
      }
    },
    [navigate]
  );

  const sourceTabs = useMemo(() => [
    { key: "all", label: "All", count: counts?.all },
    { key: "expense", label: "Expenses", count: counts?.expense },
    { key: "purchase_order", label: "Purchase Orders", count: counts?.purchase_order },
  ], [counts]);

  const agedVendors = agedData?.vendors ?? [];
  const {
    sortedData: sortedAged,
    sortKey: agedSortKey,
    sortDirection: agedSortDir,
    handleSort: agedHandleSort,
  } = useTableSort(agedVendors);

  const pageTabs = [
    { key: "summary", label: "Summary" },
    { key: "ledger", label: "Ledger" },
    { key: "aged", label: "Aged Payables" },
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
        <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <ReportPeriodPicker value={period} onChange={setPeriod} />
            <InlineSelect
              options={CURRENCY_OPTIONS}
              value={currency}
              onChange={setCurrency}
              aria-label="Currency"
            />
          </div>
        </div>
      </div>

      {exportError && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {exportError}
        </div>
      )}

      {/* Summary tab */}
      {activeTab === "summary" && (
        summaryLoading ? <LoadingState message="Loading purchases summary..." className="h-64" /> :
          summaryError ? (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
              {summaryError}
            </div>
          ) :
            summary ? (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  <MetricCard label="Actual Spend" value={fmt(summary.metrics.actual_spend)} change={null} />
                  <MetricCard label="PO Commitments" value={fmt(summary.metrics.po_commitments)} change={null} />
                  <MetricCard label="Potential Input VAT" value={fmt(summary.metrics.input_vat_estimate)} change={null} />
                  <MetricCard label="Tax in PO Commitments" value={fmt(summary.metrics.po_commitment_tax)} change={null} />
                  <MetricCard label="Expenses" value={String(summary.metrics.expense_count)} change={null} />
                  <MetricCard label="PO Commitment Count" value={String(summary.metrics.po_commitment_count)} change={null} />
                  <MetricCard label="Outstanding Payables" value={fmt(summary.metrics.outstanding_payables)} change={null} />
                </div>

                <div className="bg-white rounded-2xl border border-gray-200 p-4 overflow-hidden">
                  <h3 className="font-bold py-3 leading-6 text-lg text-gray-900">Top Vendors by Spend</h3>
                  <div className="overflow-x-auto rounded-b-lg border border-white">
                    <Table
                      columns={[
                        { key: "vendor_name", header: "Vendor" },
                        {
                          key: "amount",
                          header: `Spend (${currency})`,
                          className: "text-right",
                          render: (r) => fmt((r as { amount: string }).amount),
                        },
                      ]}
                      data={summary.spend_by_vendor}
                      rowKey={(row) => row.vendor_id}
                      emptyMessage="No vendor data for this period."
                    />
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
                tabs={sourceTabs}
                activeTab={sourceFilter}
                onTabChange={(k) => setSourceFilter(k as typeof sourceFilter)}
              />
              <div className="flex flex-col xl:flex-row items-center gap-4">
                <SearchInput
                  placeholder="Search vendor, reference..."
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
                    {
                      key: "entry_date",
                      header: "Date",
                      sortKey: "entry_date",
                      render: (r) => formatDisplayDate(r.entry_date),
                    },
                    { key: "reference", header: "Reference" },
                    { key: "entity_name", header: "Vendor", sortKey: "entity_name" },
                    {
                      key: "source_type",
                      header: "Type",
                      render: (r) => {
                        return (
                          <span className="text-xs font-medium text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full capitalize">
                            {r.source_type === "purchase_order" ? "PO" : "Expense"}
                          </span>
                        );
                      },
                    },
                    {
                      key: "status",
                      header: "Status",
                      render: (r) => {
                        return (
                          <Badge variant={r.status as BadgeVariant}>
                            {r.status}
                          </Badge>
                        );
                      },
                    },
                    {
                      key: "amount",
                      header: `Total (${currency})`,
                      sortKey: "amount",
                      className: "text-right",
                      render: (r) => fmt(r.amount),
                    },
                    {
                      key: "tax",
                      header: `Tax (${currency})`,
                      sortKey: "tax",
                      className: "text-right",
                      render: (r) => fmt(r.tax),
                    },
                    {
                      key: "balance_due",
                      header: `Balance (${currency})`,
                      sortKey: "balance_due",
                      className: "text-right",
                      render: (r) => fmt(r.balance_due),
                    },
                  ]}
                  data={sortedLedger}
                  rowKey={(r) => r.source_id}
                  onRowClick={handleLedgerRowClick}
                  sortable
                  sortKey={sortKey ?? undefined}
                  sortDirection={sortDirection}
                  onSort={handleSort}
                  emptyMessage="No purchases for this period."
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

      {/* Aged Payables tab */}
      {activeTab === "aged" && (
        <div className="space-y-6">
          {agedData && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-content-secondary">
                As of:{" "}
                <span className="font-medium text-gray-800">
                  {formatDisplayDate(agedData.as_of_date)}
                </span>
              </p>
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-3 py-1">
                Purchase orders always appear in "Current" -- no payment due date on POs
              </p>
            </div>
          )}

          {agedError && (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
              {agedError}
            </div>
          )}

          {agedLoading ? (
            <LoadingState message="Loading aged payables..." className="h-48" />
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
                      { key: "vendor_name", header: "Vendor", sortKey: "vendor_name" },
                      { key: "current", header: "Current", sortKey: "current", className: "text-right", render: (r) => fmt((r as AgedPayableRow).current) },
                      { key: "days_1_30", header: "1-30 days", sortKey: "days_1_30", className: "text-right", render: (r) => fmt((r as AgedPayableRow).days_1_30) },
                      { key: "days_31_60", header: "31-60 days", sortKey: "days_31_60", className: "text-right", render: (r) => fmt((r as AgedPayableRow).days_31_60) },
                      { key: "days_61_90", header: "61-90 days", sortKey: "days_61_90", className: "text-right", render: (r) => fmt((r as AgedPayableRow).days_61_90) },
                      { key: "days_90_plus", header: "90+ days", sortKey: "days_90_plus", className: "text-right", render: (r) => fmt((r as AgedPayableRow).days_90_plus) },
                      { key: "total", header: `Total (${currency})`, sortKey: "total", className: "text-right font-semibold", render: (r) => fmt((r as AgedPayableRow).total) },
                    ]}
                    data={sortedAged}
                    rowKey={(r) => (r as AgedPayableRow).vendor_id}
                    sortable
                    sortKey={agedSortKey ?? undefined}
                    sortDirection={agedSortDir}
                    onSort={agedHandleSort}
                    emptyMessage="No outstanding payables."
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
