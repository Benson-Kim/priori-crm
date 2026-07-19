// Utility exports

import { getTodayString, toISODateString } from "./dateUtils";

export type ReportPeriodMode = "month" | "quarter" | "year" | "custom";

export interface ReportPeriodFilter {
     mode: ReportPeriodMode;
     year: number;
     /** 1-12; required for mode=month */
     month?: number;
     /** 1-4; required for mode=quarter */
     quarter?: number;
     /** YYYY-MM-DD; required for mode=custom */
     customFrom?: string;
     /** YYYY-MM-DD; required for mode=custom */
     customTo?: string;
}



export const today = getTodayString();
export const currentYear = new Date().getUTCFullYear();
export const MIN_YEAR = currentYear - 5;

export const MONTH_SHORT = [
     "Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

export const MODE_LABELS: Record<ReportPeriodMode, string> = {
     month: "Month",
     quarter: "Quarter",
     year: "Year",
     custom: "Custom",
};


export function resolveReportPeriod(filter: ReportPeriodFilter): {
     dateFrom: string | undefined;
     dateTo: string | undefined;
} {
     const y = filter.year;

     if (filter.mode === "month" && filter.month != null) {
          const m = filter.month;
          const firstDay = new Date(Date.UTC(y, m - 1, 1));
          const lastDay = new Date(Date.UTC(y, m, 0));
          const dateFrom = toISODateString(firstDay);
          const rawTo = toISODateString(lastDay);
          return { dateFrom, dateTo: rawTo > today ? today : rawTo };
     }

     if (filter.mode === "quarter" && filter.quarter != null) {
          const startMonth = (filter.quarter - 1) * 3;
          const firstDay = new Date(Date.UTC(y, startMonth, 1));
          const lastDay = new Date(Date.UTC(y, startMonth + 3, 0));
          const dateFrom = toISODateString(firstDay);
          const rawTo = toISODateString(lastDay);
          return { dateFrom, dateTo: rawTo > today ? today : rawTo };
     }

     if (filter.mode === "year") {
          const dateFrom = `${y}-01-01`;
          const rawTo = `${y}-12-31`;
          return { dateFrom, dateTo: rawTo > today ? today : rawTo };
     }

     if (filter.mode === "custom") {
          return { dateFrom: filter.customFrom, dateTo: filter.customTo };
     }

     return { dateFrom: undefined, dateTo: undefined };
}

export function isReportPeriodReady(filter: ReportPeriodFilter): boolean {
     const { dateFrom, dateTo } = resolveReportPeriod(filter);
     return !!(dateFrom && dateTo && dateFrom <= dateTo);
}

export function buildReportPeriodParams(
     filter: ReportPeriodFilter,
     currency: string
): Record<string, string | number | boolean | undefined> {
     const { dateFrom, dateTo } = resolveReportPeriod(filter);
     return { range: "custom", dateFrom, dateTo, currency };
}

export function defaultReportPeriod(): ReportPeriodFilter {
     const now = new Date();
     return {
          mode: "month",
          year: now.getUTCFullYear(),
          month: now.getUTCMonth() + 1,
     };
}

// Label helper

function formatIsoShort(iso: string): string {
     const [, m, d] = iso.split("-").map(Number);
     return `${MONTH_SHORT[m - 1]} ${d}`;
}

export function periodLabel(filter: ReportPeriodFilter): string {
     if (filter.mode === "month" && filter.month != null) {
          return `${MONTH_SHORT[filter.month - 1]} ${filter.year}`;
     }
     if (filter.mode === "quarter" && filter.quarter != null) {
          return `Q${filter.quarter} ${filter.year}`;
     }
     if (filter.mode === "year") {
          return String(filter.year);
     }
     if (filter.mode === "custom") {
          if (filter.customFrom && filter.customTo) {
               return `${formatIsoShort(filter.customFrom)} – ${formatIsoShort(filter.customTo)}`;
          }
          return "Select range";
     }
     return "Select period";
}