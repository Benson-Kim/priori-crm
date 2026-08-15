import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { LoadingState } from "@/components/ui/LoadingState";
import { MetricCard } from "@/components/ui/MetricCard";
import { ReportPeriodPicker } from "@/components/ui/ReportPeriodPicker";
import { Table } from "@/components/ui/Table";
import { useReportingDate } from "@/hooks/useReportingDate";
import { CURRENCY_OPTIONS, DEFAULT_CURRENCY } from "@/lib/constants";
import {
  defaultReportPeriod,
  isReportPeriodReady,
  resolveReportPeriod,
  type ReportPeriodFilter,
} from "@/lib/reportUtils";
import { formatDate, formatDelta, getNameInitials, money } from "@/lib/utils";
import {
  getCashflowSeries,
  getDashboardSummary,
  getRecentTransactions,
  getTopSales,
  type CashflowSeries,
  type DashboardSummary,
  type DashboardTransaction,
  type DashboardTransactions,
  type TopSaleLine,
  type TopSales,
} from "@/services/dashboardApi";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// Widget list-size caps; the backend enforces the same limits server-side.
const RECENT_TRANSACTIONS_LIMIT = 10;
const TOP_SALES_LIMIT = 5;

// Deterministic badge palette: the same item name always maps to the same
// colour (hash over the name) so the avatar tile is stable across renders.
const BADGE_COLORS = [
  "bg-sky-500",
  "bg-cocoa",
  "bg-purple-25 text-gray-700",
  "bg-sky-blue-900",
  "bg-green-700",
] as const;

function badgeForName(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return BADGE_COLORS[hash % BADGE_COLORS.length];
}

/** Format an ISO timestamp as a short "8:17 AM" wall-clock time. */
function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-KE", { hour: "numeric", minute: "2-digit" });
}

/** X-axis label for a cashflow bucket (e.g. "3 Jan"). */
function bucketLabel(bucketStart: string): string {
  const d = new Date(`${bucketStart}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return bucketStart;
  return d.toLocaleDateString("en-KE", { day: "numeric", month: "short" });
}

/**
 * Format a signed ledger amount with an explicit sign prefix.
 * Renders "-Ksh 1,200.00" as "- Ksh 1,200.00" so the currency prefix is
 * always readable regardless of sign.
 */
function formatSignedMoney(
  value: number | string | null | undefined,
  currency: string,
): string {
  if (value == null) return "\u2014";
  const n = Number(value);
  if (Number.isNaN(n)) return "\u2014";
  const abs = Math.abs(n).toLocaleString("en-KE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const prefix = currency === "KES" ? "Ksh" : currency;
  return n < 0 ? `- ${prefix} ${abs}` : `${prefix} ${abs}`;
}

/**
 * Series colours, shared by the bars and the tooltip.
 *
 * These used to be written twice — #717bbc/#9d4d8f on the <Bar>s and the
 * near-but-not-equal #7b77c8/#a54a96 in the tooltip — so the swatch never
 * quite matched the bar it described.
 */
const CASHFLOW_COLORS = {
  income: "#717bbc",
  expense: "#9d4d8f",
} as const;

/** Tooltip heading for a bucket, e.g. "Jan 3 - 2026". */
function tooltipDateLabel(bucketStart: string): string {
  const d = new Date(`${bucketStart}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return bucketStart;
  const month = d.toLocaleDateString("en-KE", { month: "short" });
  return `${month} ${d.getUTCDate()} - ${d.getUTCFullYear()}`;
}

interface CashflowChartDatum {
  name: string;
  /** Heading the tooltip shows; carries the year the x-axis label omits. */
  fullLabel: string;
  income: number;
  expense: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; payload: CashflowChartDatum }>;
}

const CustomTooltip = ({ active, payload }: CustomTooltipProps) => {
  if (!active || !payload?.length) return null;

  const { fullLabel } = payload[0].payload;

  return (
    /*
     * Lifted above the cursor and centred on it so the pointer below the
     * card aims at the bar being described; recharts would otherwise park
     * the box down and to the right, leaving the pointer aimed at nothing.
     */
    <div className="relative z-50 -translate-x-1/2 -translate-y-[calc(100%+14px)]">
      <div className="flex min-w-36 flex-col items-center justify-center rounded-3xl bg-[#f8f9fb] px-6 py-4 shadow-[0_8px_24px_rgba(16,24,40,0.12)]">
        <p className="text-[17px] font-medium text-gray-600">{fullLabel}</p>
        <p
          className="mt-2 text-[20px] font-bold leading-7"
          style={{ color: CASHFLOW_COLORS.income }}
        >
          {money(payload[0].value)}
        </p>
        <p
          className="text-[20px] font-bold leading-7"
          style={{ color: CASHFLOW_COLORS.expense }}
        >
          {money(payload[1]?.value ?? 0)}
        </p>
      </div>

      {/* Downward pointer, same fill as the card. */}
      <span
        aria-hidden
        className="absolute left-1/2 top-full h-0 w-0 -translate-x-1/2 border-x-[10px] border-t-[12px] border-x-transparent border-t-[#f8f9fb]"
      />
    </div>
  );
};

// Section-level widget: Summary cards

interface SummaryWidgetProps {
  currency: string;
  onCurrencyChange: (value: string) => void;
}

function SummaryWidget({ currency, onCurrencyChange }: SummaryWidgetProps) {
  const reportingDay = useReportingDate();
  const [period, setPeriod] = useState<ReportPeriodFilter>(() =>
    defaultReportPeriod(reportingDay)
  );
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
  const periodReady = isReportPeriodReady(period, reportingDay);

  // Stale-response guard: only the latest request may write state.
  const seqRef = useRef(0);
  useEffect(() => {
    if (!periodReady) {
      seqRef.current++;
      setSummary(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    setIsLoading(true);
    setError(null);
    getDashboardSummary({ range: "custom" as const, dateFrom, dateTo }, currency)
      .then((data) => {
        if (seq === seqRef.current) setSummary(data);
      })
      .catch((err) => {
        if (seq === seqRef.current)
          setError(err instanceof Error ? err.message : "Failed to load summary");
      })
      .finally(() => {
        if (seq === seqRef.current) setIsLoading(false);
      });
  }, [periodReady, dateFrom, dateTo, currency]);

  return (
    <section>
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center mb-4">
        <div className="flex flex-col gap-4 sm:w-full sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-xl font-bold text-gray-800">Overview</h2>
          <div className="flex items-center gap-3">
            <InlineSelect
              options={CURRENCY_OPTIONS}
              value={currency}
              onChange={onCurrencyChange}
              aria-label="Display currency"
              triggerClassName="bg-white p-2"
            />
            <ReportPeriodPicker value={period} onChange={setPeriod} triggerClassName="bg-white p-2" />
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Balance"
          value={money(summary?.balance.amount)}
          change={isLoading ? null : formatDelta(summary?.balance.change_percent)}
        />
        <MetricCard
          label="Income"
          value={money(summary?.income.amount)}
          change={isLoading ? null : formatDelta(summary?.income.change_percent)}
        />
        <MetricCard
          label="Expenses"
          value={money(summary?.expenses.amount)}
          change={
            isLoading
              ? null
              : formatDelta(summary?.expenses.change_percent, { invert: true })
          }
        />
        <MetricCard
          label="New Customers"
          value={summary ? String(summary.new_customers.count) : "\u2014"}
          change={isLoading ? null : formatDelta(summary?.new_customers.change_percent)}
        />
      </div>
    </section>
  );
}

// Section-level widget: Cashflow chart

interface CashflowWidgetProps {
  currency: string;
}

function CashflowWidget({ currency }: CashflowWidgetProps) {
  const reportingDay = useReportingDate();
  const [period, setPeriod] = useState<ReportPeriodFilter>(() =>
    defaultReportPeriod(reportingDay)
  );
  const [series, setSeries] = useState<CashflowSeries | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
  const periodReady = isReportPeriodReady(period, reportingDay);

  const seqRef = useRef(0);
  useEffect(() => {
    if (!periodReady) {
      seqRef.current++;
      setSeries(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    setIsLoading(true);
    setError(null);
    getCashflowSeries({ range: "custom" as const, dateFrom, dateTo }, currency)
      .then((data) => {
        if (seq === seqRef.current) setSeries(data);
      })
      .catch((err) => {
        if (seq === seqRef.current)
          setError(err instanceof Error ? err.message : "Failed to load cashflow");
      })
      .finally(() => {
        if (seq === seqRef.current) setIsLoading(false);
      });
  }, [periodReady, dateFrom, dateTo, currency]);

  const chartData = useMemo<CashflowChartDatum[]>(
    () =>
      (series?.buckets ?? []).map((bucket) => ({
        name: bucketLabel(bucket.bucket_start),
        fullLabel: tooltipDateLabel(bucket.bucket_start),
        income: Number(bucket.income),
        expense: Number(bucket.expense),
      })),
    [series],
  );

  return (
    <Card padding="lg" className="relative flex flex-col justify-between rounded-xl">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between mb-4">
        <div>
          <h4 className="text-lg leading-6 text-gray-500">Cash Flow</h4>
          <p className="font-bold py-3 leading-6 text-lg text-gray-800">
            {money(series?.net_total)}
          </p>
        </div>
        <ReportPeriodPicker value={period} onChange={setPeriod} triggerClassName="bg-white p-2" />
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {isLoading ? (
        <LoadingState message="Loading cashflow..." />
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart
            data={chartData}
            margin={{ top: 10, right: 0, left: -20, bottom: 0 }}
            barGap={2}
          >
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#eaecf0" />
            <XAxis
              dataKey="name"
              axisLine={false}
              tickLine={false}
              tick={{ fill: "#667085", fontSize: 12 }}
              dy={10}
            />
            <YAxis
              tickFormatter={(value) => (value === 0 ? "0" : `${value / 1000}k`)}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "#667085", fontSize: 12 }}
            />
            <Tooltip
              content={<CustomTooltip />}
              cursor={{ fill: "transparent" }}
              offset={0}
              /*
               * The card is lifted above the cursor, so on the upper rows of
               * the plot it needs to render outside the chart's viewBox —
               * without this the heading is clipped off at the top.
               */
              allowEscapeViewBox={{ x: true, y: true }}
            />
            <Bar dataKey="income" fill={CASHFLOW_COLORS.income} radius={[20, 20, 20, 20]} barSize={16} />
            <Bar dataKey="expense" fill={CASHFLOW_COLORS.expense} radius={[20, 20, 20, 20]} barSize={16} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

// Section-level widget: Top Sales

/*
 * Same rules as the transactions table: no bg-gray-50 here (Table already
 * paints the header, and repeating it tints every body row), one line per
 * cell, and the product name clamped by width with the full value in `title`.
 */
const SALES_COLUMNS = [
  {
    key: "product",
    header: "Product",
    className: "font-normal w-[48%]",
    render: (item: TopSaleLine) => (
      <div className="flex items-center space-x-3">
        <p
          className={`h-10 w-10 shrink-0 flex items-center justify-center text-white ${badgeForName(item.item_name)
            }`}
        >
          {getNameInitials(item.item_name).slice(0, 1) || "?"}
        </p>
        <div className="flex min-w-0 flex-col">
          <span
            className="truncate text-sm leading-6 text-gray-600"
            title={item.item_name}
          >
            {item.item_name}
          </span>
          <span className="whitespace-nowrap text-xs leading-5 text-gray-500">
            {item.document_count}{" "}
            {item.document_count === 1 ? "invoice" : "invoices"}
          </span>
        </div>
      </div>
    ),
  },
  {
    key: "no_of_sales",
    header: "Sales No.",
    className: "font-normal whitespace-nowrap w-[22%]",
    render: (item: TopSaleLine) => (
      <span className="text-sm leading-6 text-gray-600">
        {Number(item.units_sold)}
      </span>
    ),
  },
  {
    key: "total_sale_amount",
    header: "Amount",
    className: "font-normal whitespace-nowrap w-[30%]",
    render: (item: TopSaleLine) => (
      <span className="font-medium text-sm leading-6 text-gray-800">
        {money(item.amount)}
      </span>
    ),
  },
];

interface TopSalesWidgetProps {
  currency: string;
}

function TopSalesWidget({ currency }: TopSalesWidgetProps) {
  const reportingDay = useReportingDate();
  const [period, setPeriod] = useState<ReportPeriodFilter>(() =>
    defaultReportPeriod(reportingDay)
  );
  const [topSales, setTopSales] = useState<TopSales | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
  const periodReady = isReportPeriodReady(period, reportingDay);

  const seqRef = useRef(0);
  useEffect(() => {
    if (!periodReady) {
      seqRef.current++;
      setTopSales(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    setIsLoading(true);
    setError(null);
    getTopSales({ range: "custom" as const, dateFrom, dateTo }, currency, TOP_SALES_LIMIT)
      .then((data) => {
        if (seq === seqRef.current) setTopSales(data);
      })
      .catch((err) => {
        if (seq === seqRef.current)
          setError(err instanceof Error ? err.message : "Failed to load top sales");
      })
      .finally(() => {
        if (seq === seqRef.current) setIsLoading(false);
      });
  }, [periodReady, dateFrom, dateTo, currency]);

  return (
    <Card padding="lg" className="relative flex flex-col gap-4 rounded-2xl">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <h3 className="font-bold py-3 leading-6 text-[20px] text-gray-800">Top Sales</h3>
        <ReportPeriodPicker value={period} onChange={setPeriod} triggerClassName="bg-white p-2" />
      </div>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {isLoading ? (
        <LoadingState message="Loading top sales..." />
      ) : (
        <Table
          columns={SALES_COLUMNS}
          data={topSales?.items ?? []}
          rowKey={(item) => item.item_name}
          className="border border-gray-200 rounded-xl"
          // Half-width card: the shared 600px floor would scroll here, and
          // fixed layout is what gives the truncated product name a width to
          // clamp against (auto layout sizes columns to content instead).
          tableClassName="min-w-0 table-fixed"
          emptyMessage="No sales available for the selected period."
        />
      )}
    </Card>
  );
}

// Section-level widget: Recent Transactions

interface TransactionsWidgetProps {
  currency: string;
}

function TransactionsWidget({ currency }: TransactionsWidgetProps) {
  const reportingDay = useReportingDate();
  const [period, setPeriod] = useState<ReportPeriodFilter>(() =>
    defaultReportPeriod(reportingDay)
  );
  const [transactions, setTransactions] = useState<DashboardTransactions | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
  const periodReady = isReportPeriodReady(period, reportingDay);

  const seqRef = useRef(0);
  useEffect(() => {
    if (!periodReady) {
      seqRef.current++;
      setTransactions(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    setIsLoading(true);
    setError(null);
    getRecentTransactions(
      { range: "custom" as const, dateFrom, dateTo },
      currency,
      RECENT_TRANSACTIONS_LIMIT,
    )
      .then((data) => {
        if (seq === seqRef.current) setTransactions(data);
      })
      .catch((err) => {
        if (seq === seqRef.current)
          setError(err instanceof Error ? err.message : "Failed to load transactions");
      })
      .finally(() => {
        if (seq === seqRef.current) setIsLoading(false);
      });
  }, [periodReady, dateFrom, dateTo, currency]);

  /*
   * One line per row. `col.className` lands on the body cells as well as the
   * header, so the header's own bg-gray-50 must not be repeated here \u2014 that is
   * what was tinting every row grey.
   *
   * Free-text columns clamp by width rather than by font size: shrinking type
   * to fit makes each row a different size and still cannot guarantee a fit
   * for a name like "St. John Paul II Sabbatical Centre". The max-width sits
   * on the span, not the <td>, because an auto-layout table does not honour
   * max-width on a cell. The full value stays in `title`.
   */
  const columns = [
    {
      key: "number",
      header: "#",
      className: "w-[64px] font-normal whitespace-nowrap",
      render: (_item: DashboardTransaction, index: number) => (
        <span className="text-sm leading-6 text-gray-800">{index + 1}.</span>
      ),
    },
    {
      key: "date",
      header: "Date & Time",
      className: "font-normal whitespace-nowrap",
      render: (item: DashboardTransaction) => (
        <span className="text-sm leading-6 text-gray-800">
          {formatDate(item.date)} - {formatTime(item.recorded_at)}
        </span>
      ),
    },
    {
      key: "id",
      header: "Reference",
      className: "font-normal whitespace-nowrap",
      render: (item: DashboardTransaction) => (
        <span
          className="block max-w-[120px] truncate text-sm leading-6 text-gray-600"
          title={item.ref_no}
        >
          {item.ref_no}
        </span>
      ),
    },
    {
      key: "product",
      header: "Product",
      className: "font-normal whitespace-nowrap",
      render: (item: DashboardTransaction) => (
        <span
          className="block max-w-[150px] truncate text-sm leading-6 text-gray-800"
          title={item.item_name ?? undefined}
        >
          {item.item_name ?? "\u2014"}
        </span>
      ),
    },
    {
      key: "customer",
      header: "Customer",
      className: "font-normal whitespace-nowrap",
      render: (item: DashboardTransaction) => (
        <span
          className="block max-w-[170px] truncate text-sm leading-6 text-gray-800"
          title={item.entity_name}
        >
          {item.entity_name}
        </span>
      ),
    },
    {
      key: "category",
      header: "Category",
      className: "font-normal whitespace-nowrap",
      render: (item: DashboardTransaction) => (
        <span
          className="block max-w-[120px] truncate text-sm leading-6 text-gray-600"
          title={item.category ?? undefined}
        >
          {item.category ?? "\u2014"}
        </span>
      ),
    },
    {
      key: "amount",
      header: "Amount",
      className: "text-right font-normal whitespace-nowrap",
      render: (item: DashboardTransaction) => (
        <span
          className="text-sm leading-6 text-gray-800"
        >
          {formatSignedMoney(item.amount, currency)}
        </span>
      ),
    },
  ];

  return (
    <div className="bg-white gap-4 px-4 py-6 flex flex-col rounded-2xl border border-gray-200 overflow-hidden">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between pr-3">
        <h3 className="font-bold p-3 leading-7.5 text-[20px] text-gray-800">
          Last Transactions
        </h3>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <ReportPeriodPicker value={period} onChange={setPeriod} triggerClassName="bg-white" />
          <Button variant="outline" className="p-2">View all</Button>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {isLoading ? (
        <LoadingState message="Loading transactions..." />
      ) : (
        <Table
          columns={columns}
          data={transactions?.items ?? []}
          rowKey={(item) => item.id}
          className="border border-gray-200 rounded-xl"
          emptyMessage="No transactions available for the selected period."
        />
      )}
    </div>
  );
}

// Page root

/**
 * DashboardPage
 *
 * Composes four independent section widgets. Each widget owns its own
 * ReportPeriodFilter state and stale-response guard, so changing the range
 * in one section never re-fetches or clears another. Currency is a
 * page-level display preference shared across all sections via props.
 */
export default function DashboardPage() {
  // Currency is a global display preference, not a per-widget filter.
  const [currency, setCurrency] = useState(DEFAULT_CURRENCY);

  return (
    <div className="space-y-6">
      <SummaryWidget
        currency={currency}
        onCurrencyChange={setCurrency}
      />

      {/* Cashflow chart + Top Sales — side by side, each with own period */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2 w-full">
        <CashflowWidget currency={currency} />
        <TopSalesWidget currency={currency} />
      </div>

      {/* Recent Transactions — own period state */}
      <TransactionsWidget currency={currency} />
    </div>
  );
}
