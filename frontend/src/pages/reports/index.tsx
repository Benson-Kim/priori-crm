/**
 * Reports hub page — /reports
 *
 * Static navigation cards linking to Sales, Purchases and Tax report pages.
 * No API calls — purely navigational.
 */

import { useNavigate } from "react-router-dom";
import { BarChart2, Receipt, ReceiptText } from "lucide-react";

const REPORT_CARDS = [
  {
    path: "/reports/sales",
    title: "Sales Report",
    description: "Revenue, invoices, outstanding balances and aged receivables.",
    icon: BarChart2,
    iconColor: "text-priori-purple",
    bgColor: "bg-purple-50",
  },
  {
    path: "/reports/purchases",
    title: "Purchases Report",
    description: "Expense and purchase order spend, vendor breakdown and aged payables.",
    icon: Receipt,
    iconColor: "text-blue-600",
    bgColor: "bg-blue-50",
  },
  {
    path: "/reports/taxes",
    title: "Tax Report",
    description: "VAT collected vs paid — net VAT position. Always in KES.",
    icon: ReceiptText,
    iconColor: "text-emerald-600",
    bgColor: "bg-emerald-50",
  },
] as const;

export default function ReportsIndexPage() {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col space-y-6">
      <div className="bg-white rounded-2xl border border-gray-200 p-4 flex flex-col gap-4">
        <p className="text-content-secondary text-base">
          Select a report to view financial insights for your business.
        </p>

        <div className="grid gap-4 sm:grid-cols-3">
          {REPORT_CARDS.map((card) => {
            const Icon = card.icon;
            return (
              <button
                key={card.path}
                type="button"
                onClick={() => navigate(card.path)}
                className="text-left rounded-2xl border border-gray-200 bg-white p-6 hover:border-priori-purple/50 hover:shadow-sm transition-all cursor-pointer"
              >
                <div className={`w-10 h-10 rounded-xl ${card.bgColor} flex items-center justify-center mb-4`}>
                  <Icon size={20} className={card.iconColor} />
                </div>
                <h3 className="font-semibold text-gray-900 text-base mb-1">{card.title}</h3>
                <p className="text-sm text-content-secondary leading-5">{card.description}</p>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
