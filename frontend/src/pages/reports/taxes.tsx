/**
 * Tax Report page -- /reports/taxes
 *
 * Displays VAT collected vs VAT paid (input tax), net VAT position,
 * and per-type breakdowns for sales and purchases.
 *
 * ALWAYS KES -- VAT is a KES obligation in Kenya.
 * No currency selector on this page.
 * No charts -- MetricCards and sortable Tables only.
 */

import {
  defaultReportPeriod,
  exportTaxReport,
  getTaxReport,
  isReportPeriodReady,
  TAX_TYPE_LABELS,
  type ReportPeriodFilter,
  type TaxReportResponse,
} from "@/services/reportsApi";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { MetricCard } from "@/components/ui/MetricCard";
import { ReportPeriodPicker } from "@/components/ui/ReportPeriodPicker";
import { Table } from "@/components/ui/Table";
import { useTableSort } from "@/hooks/useTableSort";
import { buildReportPeriodParams } from "@/lib/reportUtils";
import { formatCurrency } from "@/lib/utils";

export default function TaxReportPage() {
  const [period, setPeriod] = useState<ReportPeriodFilter>(defaultReportPeriod);
  const [data, setData] = useState<TaxReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  useEffect(() => {
    if (!isReportPeriodReady(period)) return;

    let cancelled = false;
    setIsLoading(true);
    setError(null);

    getTaxReport(period)
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load tax report");
      })
      .finally(() => { if (!cancelled) setIsLoading(false); });

    return () => { cancelled = true; };
  }, [JSON.stringify(buildReportPeriodParams(period, "KES"))]);

  const handleExport = async () => {
    if (!isReportPeriodReady(period)) return;
    setIsExporting(true);
    try {
      await exportTaxReport(period);
    } catch {
      // silent
    } finally {
      setIsExporting(false);
    }
  };

  const salesRows = data?.sales_by_tax_type ?? [];
  const purchasesRows = data?.purchases_by_tax_type ?? [];

  type TaxRow = { tax_type: string; tax_amount: string; document_count: number; label: string };

  const salesData: TaxRow[] = salesRows.map((r) => ({
    ...r,
    label: TAX_TYPE_LABELS[r.tax_type] ?? r.tax_type,
  }));
  const purchasesData: TaxRow[] = purchasesRows.map((r) => ({
    ...r,
    label: TAX_TYPE_LABELS[r.tax_type] ?? r.tax_type,
  }));

  const { sortedData: sortedSales, sortKey: sSortKey, sortDirection: sSortDir, handleSort: sHandleSort } =
    useTableSort(salesData);
  const { sortedData: sortedPurchases, sortKey: pSortKey, sortDirection: pSortDir, handleSort: pHandleSort } =
    useTableSort(purchasesData);

  const fmt = (v: string | undefined) => formatCurrency(Number(v ?? 0), "KES");

  const netVat = Number(data?.metrics.net_vat ?? 0);
  const netVatPositive = netVat > 0;

  return (
    <div className="flex flex-col space-y-6">
      {/* Header controls card */}
      <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col xl:flex-row justify-between items-start xl:items-center gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <ReportPeriodPicker value={period} onChange={setPeriod} />
          <span className="flex items-center px-3 py-3 gap-2 rounded-lg border border-gray-300 bg-gray-50 text-sm text-content-secondary leading-6">
            KES only
          </span>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleExport}
          disabled={isExporting || !data}
        >
          {isExporting ? "Exporting..." : "Export Excel"}
        </Button>
      </div>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {isLoading ? (
        <LoadingState message="Loading tax report..." className="h-64" />
      ) : data ? (
        <>
          {/* VAT Metrics */}
          <div className="grid gap-4 sm:grid-cols-3">
            <MetricCard
              label="VAT Collected (Sales)"
              value={fmt(data.metrics.vat_collected)}
              change={null}
            />
            <MetricCard
              label="VAT Paid (Purchases)"
              value={fmt(data.metrics.vat_paid)}
              change={null}
            />
            <div
              className={`relative flex flex-col justify-between rounded-2xl border px-6 py-3 ${
                netVatPositive
                  ? "border-amber-200 bg-amber-50"
                  : "border-emerald-200 bg-emerald-50"
              }`}
            >
              <p className="text-gray-500 text-[18px] py-3">Net VAT Position</p>
              <div className="py-3 flex items-center justify-between gap-3">
                <p className="font-bold text-gray-800 text-2xl">
                  {fmt(data.metrics.net_vat)}
                </p>
                <span
                  className={`text-sm font-semibold px-2 py-1 rounded-full ${
                    netVatPositive
                      ? "bg-amber-100 text-amber-700"
                      : "bg-emerald-100 text-emerald-700"
                  }`}
                >
                  {netVatPositive ? "Payable to KRA" : "Credit"}
                </span>
              </div>
            </div>
          </div>

          {/* Sales by Tax Type */}
          <div className="bg-white rounded-2xl border border-gray-200 p-4 overflow-hidden">
            <h3 className="font-bold py-3 leading-6 text-lg text-gray-900">Sales VAT by Type</h3>
            <div className="overflow-x-auto rounded-b-lg border border-white">
              <Table
                columns={[
                  { key: "label", header: "Tax Type" },
                  {
                    key: "tax_amount",
                    header: "Tax Amount (KES)",
                    sortKey: "tax_amount",
                    className: "text-right",
                    render: (r) => fmt(r.tax_amount),
                  },
                  {
                    key: "document_count",
                    header: "Invoices",
                    sortKey: "document_count",
                    className: "text-right",
                    render: (r) => r.document_count,
                  },
                ]}
                data={sortedSales}
                rowKey={(r) => r.tax_type}
                sortable
                sortKey={sSortKey ?? undefined}
                sortDirection={sSortDir}
                onSort={sHandleSort}
                emptyMessage="No sales VAT for this period."
              />
            </div>
          </div>

          {/* Purchases by Tax Type */}
          <div className="bg-white rounded-2xl border border-gray-200 p-4 overflow-hidden">
            <h3 className="font-bold py-3 leading-6 text-lg text-gray-900">Purchases VAT by Type</h3>
            <div className="overflow-x-auto rounded-b-lg border border-white">
              <Table
                columns={[
                  { key: "label", header: "Tax Type" },
                  {
                    key: "tax_amount",
                    header: "Tax Amount (KES)",
                    sortKey: "tax_amount",
                    className: "text-right",
                    render: (r) => fmt(r.tax_amount),
                  },
                ]}
                data={sortedPurchases}
                rowKey={(r) => r.tax_type}
                sortable
                sortKey={pSortKey ?? undefined}
                sortDirection={pSortDir}
                onSort={pHandleSort}
                emptyMessage="No purchase VAT for this period."
              />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
