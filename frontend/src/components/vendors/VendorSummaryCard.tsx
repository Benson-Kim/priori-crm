/**
 * VendorSummaryCard — one of the three cards on the vendor detail Overview tab
 * (Total POs / Total Payments / Total Bills). Each card is self-contained: it
 * owns its date filter, fetches its own aggregate + paginated row list, tags
 * every row paid/pending, and exports to Excel / PDF.
 *
 * The backend card endpoints take explicit period_start/period_end (the same
 * contract as the vendor statement), so the chosen period is resolved to
 * concrete dates before the request. ReportPeriodPicker drives the UX, and
 * resolveReportPeriod does the resolving - this file used to carry its own
 * copy of that switch, duplicating resolvePeriod() in statementsApi.
 */
import { Badge, type BadgeVariant } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Dropdown } from "@/components/ui/Dropdown";
import { LoadingState } from "@/components/ui/LoadingState";
import { useReportingDate } from "@/hooks/useReportingDate";
import { toISODateString } from "@/lib/dateUtils";
import {
  currentYear,
  isReportPeriodReady,
  resolveReportPeriod,
  type ReportPeriodFilter,
} from "@/lib/reportUtils";
import { formatCurrency, formatDate, saveBlob } from "@/lib/utils";
import {
  exportVendorCardExcel,
  exportVendorCardPdf,
  getVendorCard,
  type VendorCardKey,
  type VendorCardSummary,
} from "@/services/vendorApi";
import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ReportPeriodPicker } from "../ui/ReportPeriodPicker";

interface VendorSummaryCardProps {
  vendorId: string;
  card: VendorCardKey;
  title: string;
  currency: string;
}

const PER_PAGE = 5;

function stateBadge(state: "paid" | "pending") {
  const variant: BadgeVariant = state === "pending" ? "pending" : "paid";
  return <Badge variant={variant}>{state === "paid" ? "Paid" : "Pending"}</Badge>;
}

export function VendorSummaryCard({
  vendorId,
  card,
  title,
  currency,
}: Readonly<VendorSummaryCardProps>) {
  // Year-to-date gives an immediate, useful window without a 12-month load.
  const reportingDay = useReportingDate();
  const [period, setPeriod] = useState<ReportPeriodFilter>(() => ({
    mode: "year",
    year: currentYear(reportingDay),
  }));
  const [page, setPage] = useState(1);
  const [data, setData] = useState<VendorCardSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const periodParams = useCallback(() => {
    const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
    const params: { period_start?: string; period_end?: string } = {};
    if (dateFrom) params.period_start = dateFrom;
    if (dateTo) params.period_end = dateTo;
    return params;
  }, [period, reportingDay]);

  const fetchCard = useCallback(async () => {
    if (!isReportPeriodReady(period, reportingDay)) return;
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
  }, [vendorId, card, page, period, periodParams, reportingDay]);

  useEffect(() => {
    void fetchCard();
  }, [fetchCard]);

  // Reset to page 1 whenever the filter changes.
  useEffect(() => {
    setPage(1);
  }, [period]);

  const handleExport = async (kind: "excel" | "pdf") => {
    setIsExporting(true);
    try {
      const params = periodParams();
      const stamp = toISODateString(new Date());
      if (kind === "excel") {
        const blob = await exportVendorCardExcel(vendorId, card, params);
        saveBlob(blob, `${title.replace(/\s+/g, "")}_${stamp}.xlsx`);
      } else {
        const blob = await exportVendorCardPdf(vendorId, card, params);
        saveBlob(blob, `${title.replace(/\s+/g, "")}_${stamp}.pdf`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setIsExporting(false);
    }
  };

  // const total = data ? Number(data.total) : 0;
  // const paid = data ? Number(data.paid_total) : 0;
  // const pending = data ? Number(data.pending_total) : 0;
  const totalPages = data?.total_pages ?? 1;

  return (
    <Card className="rounded-2xl border border-gray-200 bg-white p-4 flex flex-col gap-4">
      {/* Header: title + total */}
      <div className="flex items-center justify-between gap-2">
        <p className="text-left text-sm font-bold text-content-primary">{title}</p>
        <Dropdown
          // disabled={isExporting || !data}
          trigger={
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-priori-purple">
              <Download size={16} />
              {isExporting ? "Exporting…" : "Export"}
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
        />

        {/* Date filter */}
        <ReportPeriodPicker value={period} onChange={setPeriod} />
      </div>

      {/* Transaction list */}
      <div className="min-h-30">
        {isLoading ? (
          <LoadingState message="Loading…" className="h-28" />
        ) : error ? (
          <p className="text-sm text-red-500 py-4">{error}</p>
        ) : data && data.items.length > 0 ? (
          <ul className="divide-y divide-gray-100">
            {data.items.map((item) => (
              <li
                key={item.id}
                className="flex items-center justify-between gap-2 py-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">
                    {item.ref_no}
                  </p>
                  <p className="text-xs text-gray-400">
                    {formatDate(item.date)}
                  </p>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-sm text-gray-700">
                    {formatCurrency(Number(item.amount), currency)}
                  </span>
                  {stateBadge(item.payment_state)}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-400 py-6 text-center">
            No records in this period.
          </p>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-gray-100 pt-2">
          <span className="text-xs text-gray-400">
            {data?.count ?? 0} total
          </span>
          <div className="flex items-center gap-2">
            <button
              className="p-1 rounded hover:bg-gray-100 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              aria-label="Previous page"
            >
              <ChevronLeft size={16} className="text-gray-600" />
            </button>
            <span className="text-xs text-gray-600">
              {page} / {totalPages}
            </span>
            <button
              className="p-1 rounded hover:bg-gray-100 disabled:opacity-40"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              aria-label="Next page"
            >
              <ChevronRight size={16} className="text-gray-600" />
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}
