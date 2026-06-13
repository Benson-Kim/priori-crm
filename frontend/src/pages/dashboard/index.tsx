import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { MetricCard } from "@/components/ui/MetricCard";
import { PeriodRangePicker } from "@/components/ui/PeriodRangePicker";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/ui/Table";
import { CURRENCY_OPTIONS, DEFAULT_CURRENCY } from "@/lib/constants";
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
import { isPeriodReady, type PeriodFilter } from "@/services/statementsApi";
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

interface CashflowChartDatum {
  name: string;
  income: number;
  expense: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; payload: CashflowChartDatum }>;
}

const CustomTooltip = ({ active, payload }: CustomTooltipProps) => {
  if (active && payload && payload.length) {
    const { name } = payload[0].payload;
    return (
      <div className="bg-white rounded-2xl p-4 shadow-[0px_4px_15px_rgba(0,0,0,0.08)] border border-gray-100 flex flex-col items-center justify-center min-w-30 relative z-50">
        <p className="text-gray-500 text-[13px] font-medium mb-1">{name}</p>
        <p className="text-[16px] font-bold" style={{ color: "#7b77c8" }}>
          {money(payload[0].value)}
        </p>
        <p className="text-[16px] font-bold" style={{ color: "#a54a96" }}>
          {money(payload[1]?.value ?? 0)}
        </p>
      </div>
    );
  }
  return null;
};

// ---------------------------------------------------------------------------
// Section-level widget: Summary cards
// ---------------------------------------------------------------------------

interface SummaryWidgetProps {
  currency: string;
}

function SummaryWidget({ currency }: SummaryWidgetProps) {
  const [period, setPeriod] = useState<PeriodFilter>({ range: "this_month" });
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { range, dateFrom, dateTo } = period;
  const periodReady = isPeriodReady(period);

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
    getDashboardSummary({ range, dateFrom, dateTo }, currency)
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
  }, [periodReady, range, dateFrom, dateTo, currency]);

  return (
    <section>
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between mb-4">
        <h2 className="text-xl font-bold text-gray-800">Overview</h2>
        <PeriodRangePicker
          period={period}
          onPeriodChange={setPeriod}
          placeholder="Select range"
          selectWrapperClassName="min-w-[220px]"
        />
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

// ---------------------------------------------------------------------------
// Section-level widget: Cashflow chart
// ---------------------------------------------------------------------------

interface CashflowWidgetProps {
  currency: string;
}

function CashflowWidget({ currency }: CashflowWidgetProps) {
  const [period, setPeriod] = useState<PeriodFilter>({ range: "this_month" });
  const [series, setSeries] = useState<CashflowSeries | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { range, dateFrom, dateTo } = period;
  const periodReady = isPeriodReady(period);

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
    getCashflowSeries({ range, dateFrom, dateTo }, currency)
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
  }, [periodReady, range, dateFrom, dateTo, currency]);

  const chartData = useMemo<CashflowChartDatum[]>(
    () =>
      (series?.buckets ?? []).map((bucket) => ({
        name: bucketLabel(bucket.bucket_start),
        income: Number(bucket.income),
        expense: Number(bucket.expense),
      })),
    [series],
  );

  return (
    <section className="relative flex flex-col p-6 justify-between rounded-2xl border border-gray-200 bg-linear-to-br from-white/25 via-white/10 to-white/05 backdrop-blur-md shadow-[4px_4px_4px_0px_rgba(0,0,0,0.02)]">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between mb-4">
        <div>
          <h4 className="text-lg leading-6 text-gray-500">Cash Flow</h4>
          <p className="font-bold py-3 leading-6 text-lg text-gray-800">
            {money(series?.net_total)}
          </p>
        </div>
        <PeriodRangePicker
          period={period}
          onPeriodChange={setPeriod}
          placeholder="Select range"
          selectWrapperClassName="min-w-[180px]"
        />
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
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
            <XAxis
              dataKey="name"
              axisLine={false}
              tickLine={false}
              tick={{ fill: "#9ca3af", fontSize: 12 }}
              dy={10}
            />
            <YAxis
              tickFormatter={(value) => (value === 0 ? "0" : `${value / 1000}k`)}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "#9ca3af", fontSize: 12 }}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: "transparent" }} />
            <Bar dataKey="income" fill="#7b77c8" radius={[20, 20, 20, 20]} barSize={10} />
            <Bar dataKey="expense" fill="#a54a96" radius={[20, 20, 20, 20]} barSize={10} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section-level widget: Top Sales
// ---------------------------------------------------------------------------

const SALES_COLUMNS = [
  {
    key: "product",
    header: "Product",
    render: (item: TopSaleLine) => (
      <div className="flex items-center space-x-3">
        <p
          className={`h-10 w-10 flex items-center justify-center text-white ${
            badgeForName(item.item_name)
          }`}
        >
          {getNameInitials(item.item_name).slice(0, 1) || "?"}
        </p>
        <div className="flex flex-col">
          <span className="text-[16px] leading-6 text-gray-600">{item.item_name}</span>
          <span className="text-[14px] leading-5 text-gray-500">
            {item.document_count}{" "}
            {item.document_count === 1 ? "invoice" : "invoices"}
          </span>
        </div>
      </div>
    ),
  },
  {
    key: "no_of_sales",
    header: "Units Sold",
    render: (item: TopSaleLine) => (
      <span className="text-[16px] leading-6 text-gray-600">
        {Number(item.units_sold)}
      </span>
    ),
  },
  {
    key: "total_sale_amount",
    header: "Amount",
    render: (item: TopSaleLine) => (
      <span className="font-medium text-[16px] leading-6 text-gray-800">
        {money(item.amount)}
      </span>
    ),
  },
];

interface TopSalesWidgetProps {
  currency: string;
}

function TopSalesWidget({ currency }: TopSalesWidgetProps) {
  const [period, setPeriod] = useState<PeriodFilter>({ range: "this_month" });
  const [topSales, setTopSales] = useState<TopSales | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { range, dateFrom, dateTo } = period;
  const periodReady = isPeriodReady(period);

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
    getTopSales({ range, dateFrom, dateTo }, currency, TOP_SALES_LIMIT)
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
  }, [periodReady, range, dateFrom, dateTo, currency]);

  return (
    <section className="relative flex flex-col gap-4 p-6 justify-between rounded-2xl border border-gray-200 bg-linear-to-br from-white/25 via-white/10 to-white/05 backdrop-blur-md shadow-[4px_4px_4px_0px_rgba(0,0,0,0.02)]">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <h3 className="font-bold py-3 leading-6 text-[20px] text-gray-800">Top Sales</h3>
        <PeriodRangePicker
          period={period}
          onPeriodChange={setPeriod}
          placeholder="Select range"
          selectWrapperClassName="min-w-[180px]"
        />
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
          emptyMessage="No sales available for the selected period."
        />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section-level widget: Recent Transactions
// ---------------------------------------------------------------------------

interface TransactionsWidgetProps {
  currency: string;
}

function TransactionsWidget({ currency }: TransactionsWidgetProps) {
  const [period, setPeriod] = useState<PeriodFilter>({ range: "this_month" });
  const [transactions, setTransactions] = useState<DashboardTransactions | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { range, dateFrom, dateTo } = period;
  const periodReady = isPeriodReady(period);

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
      { range, dateFrom, dateTo },
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
  }, [periodReady, range, dateFrom, dateTo, currency]);

  const columns = [
    {
      key: "number",
      header: "#",
      className: "w-[64px]",
      render: (_item: DashboardTransaction, index: number) => (
        <span className="text-[16px] leading-6 text-gray-800">{index + 1}.</span>
      ),
    },
    {
      key: "date",
      header: "Date & Time",
      render: (item: DashboardTransaction) => (
        <span className="text-[16px] leading-6 text-gray-800">
          {formatDate(item.date)} - {formatTime(item.recorded_at)}
        </span>
      ),
    },
    {
      key: "id",
      header: "Reference",
      render: (item: DashboardTransaction) => (
        <span className="text-[16px] leading-6 text-gray-600">{item.ref_no}</span>
      ),
    },
    {
      key: "product",
      header: "Product",
      render: (item: DashboardTransaction) => (
        <span className="text-[16px] leading-6 text-gray-800">
          {item.item_name ?? "\u2014"}
        </span>
      ),
    },
    {
      key: "customer",
      header: "Customer",
      render: (item: DashboardTransaction) => (
        <span className="text-[16px] leading-6 text-gray-800">{item.entity_name}</span>
      ),
    },
    {
      key: "website",
      header: "Website",
      render: (item: DashboardTransaction) => (
        <span className="text-[16px] leading-6 text-gray-600">
          {item.website ?? "\u2014"}
        </span>
      ),
    },
    {
      key: "amount",
      header: "Amount",
      className: "text-right",
      render: (item: DashboardTransaction) => (
        <span
          className={`text-[16px] leading-6 ${
            Number(item.amount) < 0 ? "text-error-600" : "text-gray-800"
          }`}
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
          <PeriodRangePicker
            period={period}
            onPeriodChange={setPeriod}
            placeholder="Select range"
            selectWrapperClassName="min-w-[180px]"
          />
          <Button variant="outline">View all</Button>
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

// ---------------------------------------------------------------------------
// Page root
// ---------------------------------------------------------------------------

/**
 * DashboardPage
 *
 * Composes four independent section widgets. Each widget owns its own
 * PeriodFilter state and stale-response guard, so changing the date range
 * in one section never re-fetches or clears another. Currency is a
 * page-level display preference shared across all sections via props.
 */
export default function DashboardPage() {
  // Currency is a global display preference, not a per-widget filter.
  const [currency, setCurrency] = useState(DEFAULT_CURRENCY);

  return (
    <div className="space-y-6">
      {/* Page header: currency selector only (each section has its own period picker) */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <span className="text-sm text-gray-500">Display currency</span>
        <Select
          options={CURRENCY_OPTIONS}
          value={currency}
          onChange={(event) => setCurrency(event.target.value)}
        />
      </div>

      {/* Summary metric cards — own period state */}
      <SummaryWidget currency={currency} />

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
