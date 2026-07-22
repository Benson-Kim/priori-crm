// Utility exports

import { calendarFromIso, getTodayString, toISODateString } from "./dateUtils";

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

export function currentYear(reportingDate: string): number {
     return calendarFromIso(reportingDate).year;
}

export function MIN_YEAR(reportingDate: string): number {
     return currentYear(reportingDate) - 5;
}

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

export function resolveReportPeriod(filter: ReportPeriodFilter, todayStr: string = getTodayString()): {
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
          return { dateFrom, dateTo: rawTo > todayStr ? todayStr : rawTo };
     }

     if (filter.mode === "quarter" && filter.quarter != null) {
          const startMonth = (filter.quarter - 1) * 3;
          const firstDay = new Date(Date.UTC(y, startMonth, 1));
          const lastDay = new Date(Date.UTC(y, startMonth + 3, 0));
          const dateFrom = toISODateString(firstDay);
          const rawTo = toISODateString(lastDay);
          return { dateFrom, dateTo: rawTo > todayStr ? todayStr : rawTo };
     }

     if (filter.mode === "year") {
          const dateFrom = `${y}-01-01`;
          const rawTo = `${y}-12-31`;
          return { dateFrom, dateTo: rawTo > todayStr ? todayStr : rawTo };
     }

     if (filter.mode === "custom") {
          return { dateFrom: filter.customFrom, dateTo: filter.customTo };
     }

     return { dateFrom: undefined, dateTo: undefined };
}

export function isReportPeriodReady(filter: ReportPeriodFilter, reportingDate: string = getTodayString()): boolean {
     const { dateFrom, dateTo } = resolveReportPeriod(filter, reportingDate);
     return !!(dateFrom && dateTo && dateFrom <= dateTo);
}

export function buildReportPeriodParams(
     filter: ReportPeriodFilter,
     currency: string,
     reportingDate: string = getTodayString()
): Record<string, string | number | boolean | undefined> {
     const { dateFrom, dateTo } = resolveReportPeriod(filter, reportingDate);
     return { range: "custom", dateFrom, dateTo, currency };
}

/** Return the exact sign of a decimal string without float conversion. */
export function decimalSign(value: string): -1 | 0 | 1 {
     const normalized = value.trim();
     if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) {
          throw new Error(`Invalid decimal value: ${value}`);
     }

     const negative = normalized.startsWith("-");
     const digits = normalized.replace(/^[+-]/, "").replace(".", "");
     if (!/[1-9]/.test(digits)) return 0;
     return negative ? -1 : 1;
}

export function defaultReportPeriod(reportingDate: string = getTodayString()): ReportPeriodFilter {
     const cal = calendarFromIso(reportingDate);
     return {
          mode: "month",
          year: cal.year,
          month: cal.month,
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