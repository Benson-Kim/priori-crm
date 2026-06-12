import { Select } from "@/components/ui/Select";
import { formatCurrency } from "@/lib/utils";
import {
     DEFAULT_CURRENCY,
     getStatementOverview,
     isPeriodReady,
     isValidRangePreset,
     type PeriodFilter,
     type StatementOverview,
} from "@/services/statementsApi";
import { useEffect, useRef, useState } from "react";

const DATE_RANGE_OPTIONS = [
     { label: "Last 7 Days", value: "last_7_days" },
     { label: "This Month", value: "this_month" },
     { label: "Last Month", value: "last_month" },
     { label: "This Quarter", value: "this_quarter" },
     { label: "This Year", value: "this_year" },
     { label: "Custom Range", value: "custom" },
];

interface MetricChange {
     text: string;
     tone: "positive" | "negative" | "neutral";
}

const TONE_CLASSES: Record<MetricChange["tone"], string> = {
     positive: "bg-success-50 text-success-600",
     negative: "bg-error-50 text-error-600",
     neutral: "bg-gray-100 text-gray-500",
};

/**
 * Build the delta badge for a metric. A null/undefined delta means the
 * previous period was zero — the API deliberately returns null instead of
 * a fake percentage, so we render a dash rather than 0%.
 * `invert` flips sentiment for metrics where a decrease is good (expenses).
 */
function buildChange(
     value: number | null | undefined,
     { invert = false, suffix = "%" }: { invert?: boolean; suffix?: string } = {},
): MetricChange | null {
     if (value == null) return null;
     const sign = value > 0 ? "+" : "";
     const text = `${sign}${value.toFixed(1)}${suffix}`;
     if (value === 0) return { text, tone: "neutral" };
     const isGood = invert ? value < 0 : value > 0;
     return { text, tone: isGood ? "positive" : "negative" };
}

interface MetricCardProps {
     label: string;
     value: string;
     change: MetricChange | null;
}

const MetricCard = ({ label, value, change }: MetricCardProps) => (
     <div className="relative flex flex-col justify-between rounded-2xl border border-gray-200 bg-linear-to-br from-white/25 via-white/10 to-white/05 px-6 py-3 backdrop-blur-md shadow-[4px_4px_4px_0px_rgba(0,0,0,0.02)]">
          <p className="text-gray-500 text-[18px] py-3">{label}</p>
          <div className="py-3 flex items-center justify-between gap-3">
               <p className="font-bold text-gray-800 text-2xl">{value}</p>
               <span
                    className={`text-sm font-semibold px-2 py-1 rounded-full ${change ? TONE_CLASSES[change.tone] : TONE_CLASSES.neutral}`}
               >
                    {change ? change.text : "—"}
               </span>
          </div>
     </div>
);

interface OverviewComponentProps {
     period: PeriodFilter;
     onPeriodChange: (period: PeriodFilter) => void;
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

     const { range, dateFrom, dateTo } = period;
     const periodReady = isPeriodReady(period);

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
          getStatementOverview({ range, dateFrom, dateTo }, currency)
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
     }, [periodReady, range, dateFrom, dateTo, currency]);

     const handleRangeChange = (value: string) => {
          if (!isValidRangePreset(value)) return;
          // Keep previously chosen custom dates when toggling back to custom;
          // presets never carry dates (the API rejects that combination).
          onPeriodChange(value === "custom" ? { range: value, dateFrom, dateTo } : { range: value });
     };

     const money = (value: number | string | null | undefined): string =>
          value == null || isLoading ? "—" : formatCurrency(Number(value), currency);

     const marginValue =
          isLoading || overview?.profit_margin.percent == null
               ? "—"
               : `${overview.profit_margin.percent.toFixed(1)}%`;

     return (
          <div className="space-y-4">
               <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                    <h2 className="text-xl font-bold text-gray-800">Overview</h2>
                    <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                         {range === "custom" && (
                              <div className="flex items-center gap-2">
                                   <input
                                        type="date"
                                        aria-label="Custom range start date"
                                        value={dateFrom ?? ""}
                                        max={dateTo}
                                        onChange={(e) =>
                                             onPeriodChange({ range, dateFrom: e.target.value || undefined, dateTo })
                                        }
                                        className="rounded-lg border border-gray-300 bg-gray-50 px-3 py-3 text-base text-gray-900"
                                   />
                                   <span className="text-gray-500">to</span>
                                   <input
                                        type="date"
                                        aria-label="Custom range end date"
                                        value={dateTo ?? ""}
                                        min={dateFrom}
                                        onChange={(e) =>
                                             onPeriodChange({ range, dateFrom, dateTo: e.target.value || undefined })
                                        }
                                        className="rounded-lg border border-gray-300 bg-gray-50 px-3 py-3 text-base text-gray-900"
                                   />
                              </div>
                         )}
                         <Select
                              options={DATE_RANGE_OPTIONS}
                              value={range}
                              onChange={(event) => handleRangeChange(event.target.value)}
                              wrapperClassName="min-w-[220px]"
                              placeholder="Select range"
                         />
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
                         change={isLoading ? null : buildChange(overview?.total_revenue.change_percent)}
                    />
                    <MetricCard
                         label="Total Expenses"
                         value={money(overview?.total_expenses.amount)}
                         change={
                              isLoading
                                   ? null
                                   : buildChange(overview?.total_expenses.change_percent, { invert: true })
                         }
                    />
                    <MetricCard
                         label="Net Profit/Loss"
                         value={money(overview?.net_profit.amount)}
                         change={isLoading ? null : buildChange(overview?.net_profit.change_percent)}
                    />
                    <MetricCard
                         label="Profit Margin"
                         value={marginValue}
                         change={
                              isLoading
                                   ? null
                                   : buildChange(overview?.profit_margin.change_points, { suffix: " pts" })
                         }
                    />
               </div>
          </div>
     );
};
