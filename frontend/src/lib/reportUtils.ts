// Utility exports

import { calendarFromIso, getTodayString, toISODateString } from "./dateUtils";

export type ReportPeriodMode = "recent" | "month" | "quarter" | "year" | "custom";

/**
 * Rolling windows that the calendar modes cannot express.
 *
 * month/quarter/year all name a FIXED slice of the calendar; these three are
 * relative to today and move with it. They exist because PeriodRangePicker
 * offered them and the screens converging onto this component still need
 * them — without these, "Last 12 Months" would degrade into a custom date
 * pair that stops being "last 12 months" tomorrow.
 */
export type ReportPeriodPreset = "last_7_days" | "last_month" | "last_12_months";

export const REPORT_PERIOD_PRESETS: {
     value: ReportPeriodPreset;
     label: string;
}[] = [
     { value: "last_7_days", label: "Last 7 days" },
     { value: "last_month", label: "Last month" },
     { value: "last_12_months", label: "Last 12 months" },
];

export interface ReportPeriodFilter {
     mode: ReportPeriodMode;
     year: number;
     /** 1-12; required for mode=month */
     month?: number;
     /** 1-4; required for mode=quarter */
     quarter?: number;
     /** required for mode=recent */
     preset?: ReportPeriodPreset;
     /** YYYY-MM-DD; required for mode=custom */
     customFrom?: string;
     /** YYYY-MM-DD; required for mode=custom */
     customTo?: string;
}

/** Build a filter for one rolling preset (year is carried for the panel). */
export function reportPeriodFromPreset(
     preset: ReportPeriodPreset,
     reportingDate: string = getTodayString()
): ReportPeriodFilter {
     return {
          mode: "recent",
          year: calendarFromIso(reportingDate).year,
          preset,
     };
}

export function clampReportPeriodForYear(
     filter: ReportPeriodFilter,
     year: number,
     reportingDate: string
): ReportPeriodFilter {
     // A rolling window has no year to clamp — it is relative to today.
     if (filter.mode === "recent") return filter;

     const current = calendarFromIso(reportingDate);
     if (year !== current.year) return { ...filter, year };

     if (filter.mode === "month") {
          return {
               ...filter,
               year,
               month: Math.min(filter.month ?? current.month, current.month),
          };
     }
     if (filter.mode === "quarter") {
          const currentQuarter = Math.ceil(current.month / 3);
          return {
               ...filter,
               year,
               quarter: Math.min(filter.quarter ?? currentQuarter, currentQuarter),
          };
     }
     return { ...filter, year };
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

// Key order drives the tab order in ReportPeriodPicker.
export const MODE_LABELS: Record<ReportPeriodMode, string> = {
     recent: "Recent",
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

     // Rolling windows, resolved off the reporting date rather than the
     // browser's clock. The arithmetic deliberately mirrors resolvePeriod()
     // in statementsApi so a screen moving from PeriodRangePicker to this
     // component keeps requesting exactly the same window.
     if (filter.mode === "recent" && filter.preset != null) {
          const [ty, tm, td] = todayStr.split("-").map(Number);
          const today = new Date(Date.UTC(ty, tm - 1, td));

          if (filter.preset === "last_7_days") {
               const start = new Date(today);
               start.setUTCDate(start.getUTCDate() - 6);
               return { dateFrom: toISODateString(start), dateTo: todayStr };
          }
          if (filter.preset === "last_month") {
               const start = new Date(Date.UTC(ty, tm - 2, 1));
               const end = new Date(Date.UTC(ty, tm - 1, 0));
               return {
                    dateFrom: toISODateString(start),
                    dateTo: toISODateString(end),
               };
          }
          // last_12_months
          const start = new Date(today);
          start.setUTCFullYear(start.getUTCFullYear() - 1);
          return { dateFrom: toISODateString(start), dateTo: todayStr };
     }

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

// URL round-trip
//
// The income statement drills into cashflow carrying the current period.
// Serialising the filter itself (rather than only its resolved dates) means
// the target page still shows "Aug 2026" or "Last 12 months" instead of a
// raw date pair.

export function reportPeriodToSearchParams(
     filter: ReportPeriodFilter
): Record<string, string> {
     const params: Record<string, string> = {
          mode: filter.mode,
          year: String(filter.year),
     };
     if (filter.month != null) params.month = String(filter.month);
     if (filter.quarter != null) params.quarter = String(filter.quarter);
     if (filter.preset) params.preset = filter.preset;
     if (filter.customFrom) params.dateFrom = filter.customFrom;
     if (filter.customTo) params.dateTo = filter.customTo;
     return params;
}

const MODES: ReportPeriodMode[] = ["recent", "month", "quarter", "year", "custom"];

/** Parse a filter off the URL, falling back to the default when unusable. */
export function reportPeriodFromSearchParams(
     params: URLSearchParams,
     reportingDate: string = getTodayString()
): ReportPeriodFilter {
     const mode = params.get("mode") as ReportPeriodMode | null;
     if (mode == null || !MODES.includes(mode)) {
          return defaultReportPeriod(reportingDate);
     }

     const year = Number(params.get("year"));
     const filter: ReportPeriodFilter = {
          mode,
          year: Number.isFinite(year) && year > 0
               ? year
               : calendarFromIso(reportingDate).year,
     };

     if (mode === "month") filter.month = Number(params.get("month")) || undefined;
     if (mode === "quarter") filter.quarter = Number(params.get("quarter")) || undefined;
     if (mode === "recent") {
          const preset = params.get("preset") as ReportPeriodPreset | null;
          filter.preset = REPORT_PERIOD_PRESETS.some((p) => p.value === preset)
               ? (preset as ReportPeriodPreset)
               : REPORT_PERIOD_PRESETS[0].value;
     }
     if (mode === "custom") {
          filter.customFrom = params.get("dateFrom") ?? undefined;
          filter.customTo = params.get("dateTo") ?? undefined;
     }

     // A mode missing its own field (a hand-edited or stale URL) would leave
     // the page permanently un-ready, so fall back rather than hang.
     return isReportPeriodReady(filter, reportingDate)
          ? filter
          : defaultReportPeriod(reportingDate);
}

// Label helper

function formatIsoShort(iso: string): string {
     const [, m, d] = iso.split("-").map(Number);
     return `${MONTH_SHORT[m - 1]} ${d}`;
}

export function periodLabel(filter: ReportPeriodFilter): string {
     if (filter.mode === "recent" && filter.preset != null) {
          return (
               REPORT_PERIOD_PRESETS.find((p) => p.value === filter.preset)?.label ??
               "Select period"
          );
     }
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