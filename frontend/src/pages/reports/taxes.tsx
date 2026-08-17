/**
 * Tax Report page -- /reports/taxes
 *
 * Displays output VAT, an expense-based input-VAT estimate, and type breakdowns.
 *
 * ALWAYS KES. This is not represented as a filing-ready VAT return.
 * No currency selector on this page.
 * No charts -- MetricCards and sortable Tables only.
 */

import { useReportingDate } from "@/hooks/useReportingDate";
import type { PaginatedApiResponse } from "@/lib/types";
import {
  exportTaxes,
  formatTaxTypeLabel,
  getExcludedTaxTransactions,
  getTaxReport,
  type ExcludedTaxTransaction,
  type TaxByTypeRow,
  type TaxReportResponse
} from "@/services/reportsApi";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { MetricCard } from "@/components/ui/MetricCard";
import { Pagination } from "@/components/ui/Pagination";
import { ReportPeriodPicker } from "@/components/ui/ReportPeriodPicker";
import { Table } from "@/components/ui/Table";
import { useTableSort } from "@/hooks/useTableSort";
import { buildReportPeriodParams, decimalSign, defaultReportPeriod, isReportPeriodReady, type ReportPeriodFilter } from "@/lib/reportUtils";
import { formatCurrency, formatDisplayDate } from "@/lib/utils";

export default function TaxReportPage() {
  const navigate = useNavigate();
  const reportingDay = useReportingDate();
  const [period, setPeriod] = useState<ReportPeriodFilter>(() => defaultReportPeriod(reportingDay));
  const previousReportingDay = useRef(reportingDay);
  const [data, setData] = useState<TaxReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [excluded, setExcluded] =
    useState<PaginatedApiResponse<ExcludedTaxTransaction> | null>(null);
  const [excludedPage, setExcludedPage] = useState(1);
  const [excludedPerPage, setExcludedPerPage] = useState(10);
  const [excludedLoading, setExcludedLoading] = useState(false);
  const [excludedError, setExcludedError] = useState<string | null>(null);

  useEffect(() => {
    const previousDefault = defaultReportPeriod(previousReportingDay.current);
    setPeriod((current) =>
      JSON.stringify(current) === JSON.stringify(previousDefault)
        ? defaultReportPeriod(reportingDay)
        : current
    );
    previousReportingDay.current = reportingDay;
  }, [reportingDay]);

  const periodKey = JSON.stringify(buildReportPeriodParams(period, "KES", reportingDay));

  useEffect(() => {
    if (!isReportPeriodReady(period, reportingDay)) return;

    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setData(null);

    getTaxReport(period, { reportingDate: reportingDay })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load tax report");
      })
      .finally(() => { if (!cancelled) setIsLoading(false); });

    return () => { cancelled = true; };
  }, [periodKey]);

  useEffect(() => {
    setExcludedPage(1);
  }, [periodKey]);

  useEffect(() => {
    if (data?.completeness.status !== "partial") {
      setExcluded(null);
      setExcludedError(null);
      return;
    }
    let cancelled = false;
    setExcludedLoading(true);
    setExcludedError(null);
    getExcludedTaxTransactions(period, { page: excludedPage }, excludedPerPage, reportingDay)
      .then((response) => {
        if (!cancelled) setExcluded(response);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setExcludedError(
            err instanceof Error
              ? err.message
              : "Failed to load excluded transactions"
          );
        }
      })
      .finally(() => {
        if (!cancelled) setExcludedLoading(false);
      });
    return () => { cancelled = true; };
  }, [data?.completeness.status, periodKey, excludedPage, excludedPerPage]);

  const handleExport = async () => {
    if (!isReportPeriodReady(period, reportingDay)) return;
    setIsExporting(true);
    setExportError(null);
    try {
      await exportTaxes(period, {
        reportingDate: reportingDay,
      });
    } catch (err: unknown) {
      setExportError(err instanceof Error ? err.message : "Failed to export tax report");
    } finally {
      setIsExporting(false);
    }
  };

  const salesRows = data?.sales_by_tax_type ?? [];
  const purchasesRows = data?.purchases_by_tax_type ?? [];

  type TaxRow = TaxByTypeRow & { label: string };

  const salesData: TaxRow[] = salesRows.map((r) => ({
    ...r,
    label: formatTaxTypeLabel(r),
  }));
  const purchasesData: TaxRow[] = purchasesRows.map((r) => ({
    ...r,
    label: formatTaxTypeLabel(r),
  }));

  const { sortedData: sortedSales, sortKey: sSortKey, sortDirection: sSortDir, handleSort: sHandleSort } =
    useTableSort(salesData);
  const { sortedData: sortedPurchases, sortKey: pSortKey, sortDirection: pSortDir, handleSort: pHandleSort } =
    useTableSort(purchasesData);

  const fmt = (v: string | undefined) => formatCurrency(Number(v ?? 0), "KES");

  const netVatSign = decimalSign(data?.metrics.net_vat_estimate ?? "0");
  const netVatStatus =
    netVatSign > 0
      ? "output-exceeds-input"
      : netVatSign < 0
        ? "input-exceeds-output"
        : "balanced";
  const netVatClass =
    netVatStatus === "output-exceeds-input"
      ? "border-amber-200 bg-amber-50"
      : netVatStatus === "input-exceeds-output"
        ? "border-emerald-200 bg-emerald-50"
        : "border-gray-200 bg-gray-50";
  const netVatBadgeClass =
    netVatStatus === "output-exceeds-input"
      ? "bg-amber-100 text-amber-700"
      : netVatStatus === "input-exceeds-output"
        ? "bg-emerald-100 text-emerald-700"
        : "bg-gray-200 text-gray-700";
  const netVatLabel =
    netVatStatus === "output-exceeds-input"
      ? "Output exceeds input estimate"
      : netVatStatus === "input-exceeds-output"
        ? "Input estimate exceeds output"
        : "Estimated balance is zero";

  return (
    <div className="flex flex-col space-y-6">
      {/* Header controls card */}
      <div className="bg-transparent rounded-2xl border border-transparent py-4 flex flex-col xl:flex-row justify-between items-start xl:items-center gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{data?.report_label ?? "VAT reconciliation estimate"}</p>
          {/* The filing caveat comes from the backend and must stay on the
              page: this report is an estimate, not the KRA return. Muted so
              the header stays compact. */}
          <p className="mt-1 text-sm text-gray-500">
            {data?.filing_warning ??
              "Confirm against eTIMS, customs, credit/debit notes, and the KRA auto-populated return before filing."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-6">
          <ReportPeriodPicker value={period} onChange={setPeriod} triggerClassName="px-2 py-1.5" />
          <Button size="sm" variant="outline" onClick={handleExport} disabled={isExporting || !data}>
            {isExporting ? "Exporting..." : "Export Excel"}
          </Button>
        </div>
      </div>

      {data?.completeness.status === "partial" && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-900">
          <p className="font-semibold">Partial VAT reconciliation</p>
          <p className="mt-1 text-sm">
            KES transactions are shown, but {data.completeness.excluded_document_count}{" "}
            foreign-currency VAT document(s) are excluded because historical KES
            tax-point conversion values are unavailable. Do not use the net estimate
            for filing.
          </p>
          <p className="mt-2 text-xs">
            Affected currencies: {data.completeness.excluded_currencies.join(", ")}
          </p>
        </div>
      )}

      {excludedError && (
        <div className="p-4 bg-error-50 border border-red-200 rounded-lg text-error-600">
          {excludedError}
        </div>
      )}

      {data?.completeness.status === "partial" && (
        <div className="bg-white rounded-2xl border border-gray-200 p-4 overflow-hidden">
          <h3 className="font-bold py-3 leading-6 text-lg text-gray-900">
            Excluded Transactions
          </h3>
          <p className="pb-3 text-sm text-content-secondary">
            Review these source documents and reconcile them externally. Entering
            historical KES tax-point evidence is not yet supported in the application.
          </p>
          {excludedLoading ? (
            <LoadingState message="Loading excluded transactions..." className="h-40" />
          ) : (
            <>
              <Table
                columns={[
                  { key: "document_type", header: "Type" },
                  { key: "reference", header: "Reference" },
                  { key: "number", header: "Number" },
                  {
                    key: "transaction_date",
                    header: "Date",
                    render: (row) => formatDisplayDate(row.transaction_date),
                  },
                  { key: "currency", header: "Currency" },
                  {
                    key: "original_amount",
                    header: "Original Amount",
                    className: "text-right",
                    render: (row) =>
                      formatCurrency(Number(row.original_amount), row.currency),
                  },
                  {
                    key: "original_vat_amount",
                    header: "Original VAT",
                    className: "text-right",
                    render: (row) =>
                      formatCurrency(Number(row.original_vat_amount), row.currency),
                  },
                  { key: "reason", header: "Reason" },
                ]}
                data={excluded?.items ?? []}
                rowKey={(row) => `${row.document_type}:${row.document_id}`}
                onRowClick={(row: ExcludedTaxTransaction) =>
                  navigate(
                    row.document_type === "invoice"
                      ? `/invoices/${row.document_id}`
                      : `/expenses/${row.document_id}`
                  )
                }
                emptyMessage="No excluded transactions."
              />
              {(excluded?.metadata.total_pages ?? 0) > 1 && (
                <div className="mt-4">
                  <Pagination
                    currentPage={excludedPage}
                    totalPages={excluded?.metadata.total_pages ?? 1}
                    perPage={excludedPerPage}
                    onPageChange={setExcludedPage}
                    onPerPageChange={(value) => {
                      setExcludedPerPage(value);
                      setExcludedPage(1);
                    }}
                  />
                </div>
              )}
            </>
          )}
        </div>
      )}

      {(error || exportError) && (
        <div className="p-4 bg-error-50 border border-red-200 rounded-lg text-error-600">
          {error ?? exportError}
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
              label="Potential Input VAT (Expenses)"
              value={fmt(data.metrics.input_vat_estimate)}
              change={null}
            />
            <div
              className={[
                "relative flex flex-col justify-between rounded-2xl border px-6 py-3",
                netVatClass,
              ].join(" ")}
            >
              <p className="text-gray-500 text-lg py-3">
                {data.completeness.status === "partial"
                  ? "Net VAT Estimate - KES documents only"
                  : "Net VAT Estimate"}
              </p>
              <div className="py-3 flex items-center justify-between gap-3">
                <p className="font-bold text-gray-800 text-2xl">
                  {fmt(data.metrics.net_vat_estimate)}
                </p>
                <span
                  className={
                    `text-sm font-semibold px-2 py-1 rounded-full ${netVatBadgeClass}`
                  }
                >
                  {netVatLabel}
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
                    key: "taxable_value",
                    header: "Taxable Value (KES)",
                    sortKey: "taxable_value",
                    className: "text-right",
                    render: (r) => fmt(r.taxable_value),
                  },
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
                rowKey={(r) => `${r.tax_type}:${r.tax_rate ?? ""}`}
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
            <h3 className="font-bold py-3 leading-6 text-lg text-gray-900">Expense VAT by Type</h3>
            <div className="overflow-x-auto rounded-b-lg border border-white">
              <Table
                columns={[
                  { key: "label", header: "Tax Type" },
                  {
                    key: "taxable_value",
                    header: "Taxable Value (KES)",
                    sortKey: "taxable_value",
                    className: "text-right",
                    render: (r) => fmt(r.taxable_value),
                  },
                  {
                    key: "tax_amount",
                    header: "Tax Amount (KES)",
                    sortKey: "tax_amount",
                    className: "text-right",
                    render: (r) => fmt(r.tax_amount),
                  },
                  {
                    key: "document_count",
                    header: "Documents",
                    sortKey: "document_count",
                    className: "text-right",
                    render: (r) => r.document_count,
                  },
                ]}
                data={sortedPurchases}
                rowKey={(r) => `${r.tax_type}:${r.tax_rate ?? ""}`}
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
