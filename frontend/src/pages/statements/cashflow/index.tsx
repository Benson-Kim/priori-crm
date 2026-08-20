import { FilterTabs } from "@/components/ui/FilterTabs";
import { LoadingState } from "@/components/ui/LoadingState";
import { Pagination } from "@/components/ui/Pagination";
import { SearchInput } from "@/components/ui/SearchInput";
import { Table } from "@/components/ui/Table";
import { useReportingDate } from "@/hooks/useReportingDate";
import {
     isReportPeriodReady,
     reportPeriodFromSearchParams,
     resolveReportPeriod,
     type ReportPeriodFilter,
} from "@/lib/reportUtils";
import { formatDisplayDate } from "@/lib/utils";
import {
     getCashflow,
     getCashflowCounts,
     type CashflowCategory,
     type CashflowCounts,
     type CashflowEntry,
} from "@/services/statementsApi";
import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { OverviewComponent } from "../components/OverviewComponent";

const CATEGORY_VALUES: readonly CashflowCategory[] = ["all", "income", "expense"];

function isCashflowCategory(value: string | null): value is CashflowCategory {
     return value != null && (CATEGORY_VALUES as readonly string[]).includes(value);
}

/**
 * Ledger amounts are signed by the API (income positive, expense negative);
 * format the magnitude and keep the sign, matching the design.
 */
const formatAmount = (amount: number): string => {
     const formatted = Math.abs(amount).toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
     });
     return amount < 0 ? `-${formatted}` : formatted;
};

export default function CashflowPage() {
     const navigate = useNavigate();
     const [searchParams] = useSearchParams();

     // Filters may arrive via the URL (income-statement drill-down).
     // reportPeriodFromSearchParams falls back to the default for anything
     // missing, unparseable or stale, so a hand-edited URL cannot leave the
     // page permanently un-ready.
     const reportingDay = useReportingDate();
     const [period, setPeriod] = useState<ReportPeriodFilter>(() =>
          reportPeriodFromSearchParams(searchParams, reportingDay)
     );
     const [categoryFilter, setCategoryFilter] = useState<CashflowCategory>(() => {
          const category = searchParams.get("category");
          return isCashflowCategory(category) ? category : "all";
     });
     const [accountCategory, setAccountCategory] = useState<string | null>(
          () => searchParams.get("accountCategory"),
     );

     const [search, setSearch] = useState("");
     const [debouncedSearch, setDebouncedSearch] = useState("");
     const [currentPage, setCurrentPage] = useState(1);
     const [perPage, setPerPage] = useState(10);

     const [entries, setEntries] = useState<CashflowEntry[]>([]);
     const [totalPages, setTotalPages] = useState(1);
     const [counts, setCounts] = useState<CashflowCounts>({ all: 0, income: 0, expense: 0 });

     const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
     const periodReady = isReportPeriodReady(period, reportingDay);

     // Debounce search so the ledger query is not hit on every keystroke.
     useEffect(() => {
          const timer = setTimeout(() => setDebouncedSearch(search), 300);
          return () => clearTimeout(timer);
     }, [search]);

     // Any filter change restarts pagination from the first page. Render-time
     // adjustment (react.dev "adjusting state when props change") instead of
     // an effect, so the reset lands before the fetch below ever sees the
     // stale page number.
     const filterKey = [
          categoryFilter,
          debouncedSearch,
          accountCategory ?? "",
          dateFrom ?? "",
          dateTo ?? "",
     ].join("|");
     const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
     if (filterKey !== prevFilterKey) {
          setPrevFilterKey(filterKey);
          setCurrentPage(1);
     }

     // Identity of the ledger window the UI currently wants; null while the
     // period is incomplete. `settled` records which request last finished,
     // so isLoading/error derive during render instead of being set
     // synchronously inside the effect (react-hooks/set-state-in-effect).
     const requestKey = periodReady ? `${filterKey}|${currentPage}|${perPage}` : null;
     const [settled, setSettled] = useState<{ key: string; error: string | null } | null>(
          null,
     );

     // Clear the stale ledger the moment the period becomes un-ready
     // (previously a synchronous reset inside the effect).
     if (requestKey === null && entries.length > 0) {
          setEntries([]);
     }
     if (requestKey === null && totalPages !== 1) {
          setTotalPages(1);
     }

     // Ledger window. Stale-response guard: the cleanup cancels superseded
     // requests, so rapid filter changes never paint out-of-order pages.
     useEffect(() => {
          if (requestKey === null) return;
          let cancelled = false;
          getCashflow({
               period: { range: "custom", dateFrom, dateTo },
               category: categoryFilter,
               accountCategory: accountCategory ?? undefined,
               search: debouncedSearch || undefined,
               page: currentPage,
               perPage,
          })
               .then((data) => {
                    if (cancelled) return;
                    setEntries(data.items);
                    setTotalPages(data.metadata.total_pages ?? 1);
                    setSettled({ key: requestKey, error: null });
               })
               .catch((err) => {
                    if (cancelled) return;
                    setSettled({
                         key: requestKey,
                         error: err instanceof Error ? err.message : "Failed to load cashflow",
                    });
               });
          return () => {
               cancelled = true;
          };
     }, [
          requestKey,
          dateFrom,
          dateTo,
          categoryFilter,
          accountCategory,
          debouncedSearch,
          currentPage,
          perPage,
     ]);

     const isLoading = requestKey !== null && settled?.key !== requestKey;
     const error =
          requestKey !== null && settled?.key === requestKey ? settled.error : null;

     // Tab counts (separate endpoint so the list query never pays a
     // mandatory COUNT). Counts ignore the active category tab by design.
     const countsSeq = useRef(0);
     useEffect(() => {
          if (!periodReady) return;
          const seq = ++countsSeq.current;
          getCashflowCounts({
               period: { range: "custom", dateFrom, dateTo },
               accountCategory: accountCategory ?? undefined,
               search: debouncedSearch || undefined,
          })
               .then((data) => {
                    if (seq === countsSeq.current) setCounts(data);
               })
               .catch((err) => {
                    // Counts are decorative; the list error banner covers failures.
                    console.error("Failed to fetch cashflow counts:", err);
               });
     }, [periodReady, dateFrom, dateTo, accountCategory, debouncedSearch]);

     const handleCategoryChange = useCallback((key: string) => {
          if (isCashflowCategory(key)) setCategoryFilter(key);
     }, []);

     const handlePerPageChange = useCallback((value: number) => {
          setPerPage(value);
          setCurrentPage(1);
     }, []);

     const handleRowClick = useCallback(
          (entry: CashflowEntry) => {
               const route = entry.source_type === "quote_invoice" ? "/invoices" : "/expenses";
               navigate(`${route}/${entry.source_id}`);
          },
          [navigate],
     );

     const categoryTabs = [
          { key: "all", label: "All", count: counts.all },
          { key: "income", label: "Income", count: counts.income },
          { key: "expense", label: "Expense", count: counts.expense },
     ];

     const columns = [
          {
               key: "number",
               header: "#",
               className: "w-[64px]",
               render: (_item: CashflowEntry, index: number) => (
                    <span className="text-gray-800">{(currentPage - 1) * perPage + index + 1}.</span>
               ),
          },
          {
               key: "entityName",
               header: "Entity/Activity",
               render: (item: CashflowEntry) => (
                    <span className="text-gray-800">{item.entity_name}</span>
               ),
          },
          {
               key: "refNo",
               header: "Ref/Description",
               render: (item: CashflowEntry) => (
                    <span className="text-gray-500">{item.ref_no}</span>
               ),
          },
          {
               key: "category",
               header: "Category",
               render: (item: CashflowEntry) => (
                    <span>{item.category === "income" ? "Income" : "Expense"}</span>
               ),
          },
          {
               key: "date",
               header: "Date",
               render: (item: CashflowEntry) => (
                    <span className="text-gray-800">{formatDisplayDate(item.date)}</span>
               ),
          },
          {
               key: "amount",
               header: "Amount",
               className: "text-right",
               render: (item: CashflowEntry) => {
                    const amount = Number(item.amount);
                    return (
                         <span className={amount > 0 ? "text-success-600" : "text-error-600"}>
                              {formatAmount(amount)}
                         </span>
                    );
               },
          },
     ];

     return (
          <div className="space-y-6">
               <OverviewComponent period={period} onPeriodChange={setPeriod} />

               {error && (
                    <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
                         {error}
                    </div>
               )}

               <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
                    <div className="flex items-center gap-3 flex-wrap">
                         <FilterTabs
                              tabs={categoryTabs}
                              activeTab={categoryFilter}
                              onTabChange={handleCategoryChange}
                         />
                         {accountCategory && (
                              <button
                                   onClick={() => setAccountCategory(null)}
                                   className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-pink-25 border border-gray-200 text-sm text-priori-purple cursor-pointer"
                                   title="Clear account category filter"
                              >
                                   {accountCategory}
                                   <X size={14} />
                              </button>
                         )}
                    </div>

                    <div className="flex items-center gap-4 w-full xl:w-auto">
                         <SearchInput
                              placeholder="Search cashflow"
                              value={search}
                              onSearchChange={setSearch}
                         />
                    </div>
               </div>

               <div className="overflow-x-auto rounded-b-lg border border-white">
                    {isLoading ? (
                         <LoadingState message="Loading cashflow..." />
                    ) : (
                         <Table
                              columns={columns}
                              data={entries}
                              rowKey={(item) => item.id}
                              onRowClick={handleRowClick}
                              emptyMessage={
                                   periodReady
                                        ? "No cashflow entries found for the selected filters."
                                        : "Select both start and end dates to view the custom range."
                              }
                         />
                    )}
               </div>

               <div className="py-3 px-4 border border-gray-200 mt-auto bg-white rounded-2xl ">
                    <Pagination
                         currentPage={currentPage}
                         totalPages={totalPages}
                         perPage={perPage}
                         onPageChange={setCurrentPage}
                         onPerPageChange={handlePerPageChange}
                    />
               </div>
          </div>
     );
}
