import { LoadingState } from "@/components/ui/LoadingState";
import { Table } from "@/components/ui/Table";
import { useReportingDate } from "@/hooks/useReportingDate";
import {
     defaultReportPeriod,
     isReportPeriodReady,
     reportPeriodToSearchParams,
     resolveReportPeriod,
     type ReportPeriodFilter,
} from "@/lib/reportUtils";
import { money } from "@/lib/utils";
import {
     getIncomeStatement,
     type IncomeStatement,
     type IncomeStatementLine,
} from "@/services/statementsApi";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { OverviewComponent } from "../components/OverviewComponent";

export default function IncomeStatementsPage() {
     const navigate = useNavigate();
     const reportingDay = useReportingDate();
     const [period, setPeriod] = useState<ReportPeriodFilter>(() =>
          defaultReportPeriod(reportingDay)
     );
     const [statement, setStatement] = useState<IncomeStatement | null>(null);
     const [isLoading, setIsLoading] = useState(true);
     const [error, setError] = useState<string | null>(null);

     const { dateFrom, dateTo } = resolveReportPeriod(period, reportingDay);
     const periodReady = isReportPeriodReady(period, reportingDay);

     // Stale-response guard: only the latest request may write state.
     const seqRef = useRef(0);
     useEffect(() => {
          if (!periodReady) {
               seqRef.current++;
               setStatement(null);
               setError(null);
               setIsLoading(false);
               return;
          }
          const seq = ++seqRef.current;
          setIsLoading(true);
          setError(null);
          getIncomeStatement({ range: "custom", dateFrom, dateTo })
               .then((data) => {
                    if (seq === seqRef.current) setStatement(data);
               })
               .catch((err) => {
                    if (seq === seqRef.current) {
                         setError(
                              err instanceof Error ? err.message : "Failed to load income statement",
                         );
                    }
               })
               .finally(() => {
                    if (seq === seqRef.current) setIsLoading(false);
               });
     }, [periodReady, dateFrom, dateTo]);

     // Drill down into the cashflow ledger filtered to this account
     // category, carrying the current period (including custom dates).
     const handleRowClick = useCallback(
          (item: IncomeStatementLine, category: "income" | "expense") => {
               const params = new URLSearchParams({
                    category,
                    accountCategory: item.account_category,
                    ...reportPeriodToSearchParams(period),
               });
               navigate(`/cashflow?${params.toString()}`);
          },
          [navigate, period],
     );

     const buildColumns = (amountClass: string) => [
          {
               key: "number",
               header: "#",
               className: "w-[64px]",
               render: (_item: IncomeStatementLine, index: number) => (
                    <span className="text-gray-800">{index + 1}.</span>
               ),
          },
          {
               key: "accountCategory",
               header: "Account Category",
               render: (item: IncomeStatementLine) => (
                    <span className="text-gray-800">{item.account_category}</span>
               ),
          },
          {
               key: "description",
               header: "Description",
               render: (item: IncomeStatementLine) => (
                    <span className="text-gray-500">{item.description ?? "—"}</span>
               ),
          },
          {
               key: "amount",
               header: "Amount",
               className: "text-right",
               render: (item: IncomeStatementLine) => (
                    <span className={amountClass}>
                         {money(Number(item.amount))}
                    </span>
               ),
          },
     ];

     const revenueColumns = buildColumns("text-success-600");
     const expenseColumns = buildColumns("text-error-600");

     const revenueFooter = statement ? (
          <tfoot>
               <tr className="bg-gray-50 border-t-2 border-green-200">
                    <td colSpan={3} className="p-3 text-[16px] font-bold text-gray-800">
                         Total Revenue
                    </td>
                    <td className="p-3 text-[16px] font-bold text-success-600 text-right">
                         {money(Number(statement.operating_revenue.total))}
                    </td>
               </tr>
          </tfoot>
     ) : null;

     const expenseFooter = statement ? (
          <tfoot>
               <tr className="bg-gray-50 border-t-2 border-red-200">
                    <td colSpan={3} className="p-3 text-[16px] font-bold text-gray-800">
                         Total Expenses
                    </td>
                    <td className="p-3 text-[16px] font-bold text-error-600 text-right">
                         {money(Number(statement.operating_expenses.total))}
                    </td>
               </tr>
          </tfoot>
     ) : null;

     return (
          <div className="space-y-6">
               <OverviewComponent period={period} onPeriodChange={setPeriod} />

               {error && (
                    <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
                         {error}
                    </div>
               )}

               {isLoading ? (
                    <LoadingState message="Loading income statement..." />
               ) : statement ? (
                    <>
                         <div className="bg-white p-4 rounded-2xl border border-gray-200 overflow-hidden">
                              <h3 className="font-bold py-3 leading-6 text-lg text-success-600">
                                   Revenue
                              </h3>
                              <Table
                                   columns={revenueColumns}
                                   data={statement.operating_revenue.line_items}
                                   rowKey={(item) => item.account_category}
                                   onRowClick={(item) => handleRowClick(item, "income")}
                                   rowClassName={() => "bg-green-25 hover:bg-success-50"}
                                   footer={revenueFooter}
                                   emptyMessage="No revenue found for the selected period."
                                   className="border border-gray-200 rounded-xl"
                              />
                         </div>

                         <div className="bg-white p-4 rounded-2xl border border-gray-200 overflow-hidden">
                              <h3 className="font-bold py-3 leading-6 text-lg text-red-600">
                                   Expenses
                              </h3>
                              <Table
                                   columns={expenseColumns}
                                   data={statement.operating_expenses.line_items}
                                   rowKey={(item) => item.account_category}
                                   onRowClick={(item) => handleRowClick(item, "expense")}
                                   rowClassName={() => "bg-red-25 hover:bg-error-50"}
                                   footer={expenseFooter}
                                   emptyMessage="No expenses found for the selected period."
                                   className="border border-gray-200 rounded-xl"
                              />
                         </div>
                    </>
               ) : !error ? (
                    <div className="p-4 bg-white border border-gray-200 rounded-2xl text-gray-500">
                         Select both start and end dates to view the custom range.
                    </div>
               ) : null}
          </div>
     );
}
