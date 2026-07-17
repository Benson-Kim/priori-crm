import { DEFAULT_DUE_DATE_DAYS } from "./constants";

const MS_PER_DAY = 24 * 60 * 60 * 1000;

export const DATE_RANGE_OPTIONS = [
  { label: "Last 7 Days", value: "last_7_days" },
  { label: "This Month", value: "this_month" },
  { label: "Last Month", value: "last_month" },
  { label: "This Quarter", value: "this_quarter" },
  { label: "This Year", value: "this_year" },
  { label: "Last 12 Months", value: "last_12_months" },
  { label: "Custom Range", value: "custom" },
];

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
