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
import { useEffect, useRef, useState } from "react";
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
     const [isLoading, setIsLoading] = useState(false);
     const [error, setError] = useState<string | null>(null);

     const reportingDay = useReportingDate();
     const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
     const periodReady = isReportPeriodReady(period, reportingDay);

     // Stale-response guard: only the latest request may write state, so
     // rapid period switching can never paint out-of-order results.
     const seqRef = useRef(0);
     useEffect(() => {
          if (!periodReady) {
               seqRef.current++;
               setOverview(null);
               setError(null);
               setIsLoading(false);
               return;
          }
          const seq = ++seqRef.current;
          setIsLoading(true);
          setError(null);
          getStatementOverview({ range: "custom", dateFrom, dateTo }, currency)
               .then((data) => {
                    if (seq === seqRef.current) setOverview(data);
               })
               .catch((err) => {
                    if (seq === seqRef.current) {
                         setError(err instanceof Error ? err.message : "Failed to load overview");
                    }
               })
               .finally(() => {
                    if (seq === seqRef.current) setIsLoading(false);
               });
     }, [periodReady, dateFrom, dateTo, currency]);

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
