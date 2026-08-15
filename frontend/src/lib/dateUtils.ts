import { DEFAULT_DUE_DATE_DAYS } from "./constants";

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/**
 * Format a Date object as an ISO date string (YYYY-MM-DD).
 * Uses UTC methods to avoid timezone-shift bugs where local midnight
 * resolves to the previous day in UTC.
 */
export function toISODateString(date: Date): string {
  const year = date.getUTCFullYear();
  // getUTCMonth() is 0-indexed — add 1 and pad to 2 digits
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Return today's date as an ISO date string.
 * Accepts an optional `now` timestamp (milliseconds) so callers can
 * inject a fixed point in time — making any function that calls this
 * fully deterministic and testable without mocking global Date.
 */
export function getTodayString(now: number = Date.now()): string {
  return toISODateString(new Date(now));
}

/**
 * Return the date N days from a given ISO date string.
 * Pure: depends only on its arguments, never on the current time.
 *
 * @param fromDateString - Base date in YYYY-MM-DD format
 * @param days           - Number of days to add (may be negative)
 * @returns ISO date string
 *
 * @example
 * addDays("2026-01-01", 30) // → "2026-01-31"
 * addDays("2026-03-01", -1) // → "2026-02-28"
 */
export function addDays(fromDateString: string, days: number): string {
  // Parse as UTC noon to avoid DST boundary issues
  const base = new Date(`${fromDateString}T12:00:00Z`);
  const result = new Date(base.getTime() + days * MS_PER_DAY);
  return toISODateString(result);
}

/**
 * Return the default due date string: today + N days.
 * Accepts optional `now` for testability.
 *
 * @param termDays - Payment term in days (default: 30)
 * @param now      - Override for current timestamp (ms); defaults to Date.now()
 */
export function getDefaultDueDate(
  termDays: number = DEFAULT_DUE_DATE_DAYS,
  now: number = Date.now()
): string {
  const today = getTodayString(now);
  return addDays(today, termDays);
}

/**
 * Check whether a due date string is before a transaction date string.
 * Pure predicate — used for form validation.
 */
export function isDueDateBeforeTransactionDate(
  dueDate: string,
  transactionDate: string
): boolean {
  return dueDate < transactionDate; // ISO strings compare lexicographically
}

/**
 * Calculate days overdue from a due date string.
 * Returns 0 if not overdue.
 * Accepts optional `now` for testability.
 */
export function getDaysOverdue(
  dueDateString: string,
  now: number = Date.now()
): number {
  const today = getTodayString(now);
  if (dueDateString >= today) return 0;
  const due = new Date(`${dueDateString}T12:00:00Z`);
  const current = new Date(`${today}T12:00:00Z`);
  return Math.floor((current.getTime() - due.getTime()) / MS_PER_DAY);
}


export function buildStatementParams(periodStart?: string, periodEnd?: string) {
  const params: Record<string, string> = {};

  if (periodStart) params.period_start = periodStart;
  if (periodEnd) params.period_end = periodEnd;

  return params;
}

export interface ReportingCalendar {
  isoDate: string;
  year: number;
  month: number;
  day: number;
  quarter: number;
}

export function calendarFromIso(isoDate: string): ReportingCalendar {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) throw new Error(`Invalid ISO reporting date: ${isoDate}`);
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  return {
    isoDate,
    year,
    month,
    day,
    quarter: Math.ceil(month / 3),
  };
}

export function reportingDateInTimeZone(
  timeZone: string,
  now: Date = new Date()
): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(
    parts
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value])
  );
  return `${values.year}-${values.month}-${values.day}`;
}

export function localCalendarDate(now: Date = new Date()): string {
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}

export function resolvedReportingDate(
  configuredTimeZone: string | undefined,
  serverDate: string | undefined,
  localFallback?: string,
  now: Date = new Date()
): string {
  if (configuredTimeZone) {
    try {
      return reportingDateInTimeZone(configuredTimeZone, now);
    } catch {
      // Fall through to the server-provided accounting date. A malformed
      // timezone must never make report utilities invent another zone.
    }
  }
  return serverDate ?? localFallback ?? localCalendarDate(now);
}

export function isDateOutOfBounds(
  iso: string,
  min?: string,
  max?: string
): boolean {
  return Boolean((min != null && iso < min) || (max != null && iso > max));
}

export function addIsoDays(iso: string, deltaDays: number): string {
  const date = new Date(`${iso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + deltaDays);
  return [
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, "0"),
    String(date.getUTCDate()).padStart(2, "0"),
  ].join("-");
}

export function preferredCalendarFocusDate(
  visibleDates: readonly string[],
  selected: string | undefined,
  today: string,
  min?: string,
  max?: string
): string | undefined {
  const enabled = visibleDates.filter(
    (date) => !isDateOutOfBounds(date, min, max)
  );
  if (selected && enabled.includes(selected)) return selected;
  if (enabled.includes(today)) return today;
  return enabled[0];
}

export interface FocusTarget {
  focus: () => void;
}

export function restoreTriggerFocus(
  target: FocusTarget | null | undefined,
  schedule: (callback: () => void) => unknown = (callback) =>
    requestAnimationFrame(callback)
): void {
  if (!target) return;
  schedule(() => target.focus());
}

export type CalendarKeyboardIntent =
  | "move"
  | "close"
  | "select"
  | "leave"
  | "none";

export function calendarKeyboardIntent(key: string): CalendarKeyboardIntent {
  if (key === "Escape") return "close";
  if (key === "Enter" || key === " ") return "select";
  if (key === "Tab") return "leave";
  if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(key)) {
    return "move";
  }
  return "none";
}

export function nextKeyboardDate(
  iso: string,
  key: string
): string | undefined {
  const deltas: Record<string, number> = {
    ArrowLeft: -1,
    ArrowRight: 1,
    ArrowUp: -7,
    ArrowDown: 7,
  };
  const delta = deltas[key];
  return delta == null ? undefined : addIsoDays(iso, delta);
}
