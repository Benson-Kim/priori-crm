import { ReportPeriodPicker } from "@/components/ui/ReportPeriodPicker";
import { DEFAULT_CURRENCY } from "@/lib/constants";
import { formatDelta, money } from "@/lib/utils";
import { useReportingDate } from "@/hooks/useReportingDate";
import {
     isReportPeriodReady,
     resolveReportPeriod,
     type ReportPeriodFilter,
} from "@/lib/reportUtils";
import {
     getStatementOverview,
     type StatementOverview,
} from "@/services/statementsApi";
import { useEffect, useState } from "react";
import { MetricCard } from "@/components/ui/MetricCard";


interface OverviewComponentProps {
     period: ReportPeriodFilter;
     onPeriodChange: (period: ReportPeriodFilter) => void;
     currency?: string;
}

/**
 * Overview cards shared by the Income Statement and Cashflow pages.
 *
 * Owns its own fetch of GET /statements/overview for the supplied period;
 * the parent page owns the period state so the page's own queries stay in
 * lock-step with the cards.
 */
export const OverviewComponent = ({
     period,
     onPeriodChange,
     currency = DEFAULT_CURRENCY,
}: OverviewComponentProps) => {
     const [overview, setOverview] = useState<StatementOverview | null>(null);

     const reportingDay = useReportingDate();
     const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
     const periodReady = isReportPeriodReady(period, reportingDay);

     // Identity of the fetch the UI currently wants; null while the period
     // is incomplete. `settled` records which request last finished, so
     // isLoading/error derive during render instead of being set
     // synchronously inside the effect (react-hooks/set-state-in-effect).
     const requestKey = periodReady ? `${dateFrom}|${dateTo}|${currency}` : null;
     const [settled, setSettled] = useState<{ key: string; error: string | null } | null>(
          null,
     );

     // Clear stale cards the moment the period becomes un-ready (previously
     // a synchronous reset inside the effect). Render-time adjustment per
     // react.dev "adjusting state when props change".
     if (requestKey === null && overview !== null) {
          setOverview(null);
     }

     // Stale-response guard: the cleanup cancels superseded requests, so
     // rapid period switching can never paint out-of-order results.
     useEffect(() => {
          if (requestKey === null) return;
          let cancelled = false;
          getStatementOverview({ range: "custom", dateFrom, dateTo }, currency)
               .then((data) => {
                    if (cancelled) return;
                    setOverview(data);
                    setSettled({ key: requestKey, error: null });
               })
               .catch((err) => {
                    if (cancelled) return;
                    setSettled({
                         key: requestKey,
                         error: err instanceof Error ? err.message : "Failed to load overview",
                    });
               });
          return () => {
               cancelled = true;
          };
     }, [requestKey, dateFrom, dateTo, currency]);

     const isLoading = requestKey !== null && settled?.key !== requestKey;
     const error =
          requestKey !== null && settled?.key === requestKey ? settled.error : null;

     const marginValue =
          isLoading || overview?.profit_margin.percent == null
               ? "—"
               : `${overview.profit_margin.percent.toFixed(1)}%`;

     return (
          <div className="space-y-4">
               <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                    <h2 className="text-xl font-bold text-gray-800">Overview</h2>
                    <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                         <ReportPeriodPicker value={period} onChange={onPeriodChange} />
                    </div>
               </div>

               {error && (
                    <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
                         {error}
                    </div>
               )}

               <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    <MetricCard
                         label="Total Revenue"
                         value={money(overview?.total_revenue.amount)}
                         change={isLoading ? null : formatDelta(overview?.total_revenue.change_percent)}
                    />
                    <MetricCard
                         label="Total Expenses"
                         value={money(overview?.total_expenses.amount)}
                         change={
                              isLoading
                                   ? null
                                   : formatDelta(overview?.total_expenses.change_percent, { invert: true })
                         }
                    />
                    <MetricCard
                         label="Net Profit/Loss"
                         value={money(overview?.net_profit.amount)}
                         change={isLoading ? null : formatDelta(overview?.net_profit.change_percent)}
                    />
                    <MetricCard
                         label="Profit Margin"
                         value={marginValue}
                         change={
                              isLoading
                                   ? null
                                   : formatDelta(overview?.profit_margin.change_points, { suffix: " pts" })
                         }
                    />
               </div>
          </div>
     );
};
