import type { MetricChange } from "@/components/ui/MetricCard";
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { DEFAULT_CURRENCY } from "./constants";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const focusInput = [
  "focus:ring-2",
  "focus:ring-blue-200 focus:dark:ring-blue-700/30",
  "focus:border-blue-500 focus:dark:border-blue-700",
];

export const focusRing = [
  "outline outline-offset-2 outline-0 focus-visible:outline-2",
  "outline-blue-500 dark:outline-blue-500",
];

export const hasErrorInput = [
  "ring-2",
  "border-red-500 dark:border-red-700",
  "ring-red-200 dark:ring-red-700/30",
];

export function createSearchParams(
  params: Record<string, string | number | boolean | null | undefined>
) {
  const stringParams: Record<string, string> = {};

  Object.keys(params).forEach((key) => {
    const value = params[key];
    if (value != null && value !== "") {
      stringParams[key] = String(value);
    }
  });

  return new URLSearchParams(stringParams).toString();
}

export const wait = (ms: number) =>
  new Promise((resolve) => setTimeout(resolve, ms));

export const getNameInitials = (name: string) => {
  return name
    .split(" ")
    .map((n) => n.charAt(0))
    .join("");
};

export function getDayNames(days: number[]): string[] {
  const dayNames = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
  ];
  return days.map((day) => dayNames[day] || "Invalid day");
}

export const validateDate = (date: string | null | undefined) => {
  if (!date) return null;
  const date_ = new Date(date);
  if (Number.isNaN(date_.getTime())) return null;
  return date;
};

export const validateInt = (value: string | null | undefined) => {
  if (!value) return null;
  const intValue = Number.parseInt(value, 10);
  if (Number.isNaN(intValue)) return null;
  return value;
};

export const formatDate = (dateString: string) => {
  if (!dateString) return "";
  const date = new Date(dateString);
  return date.toLocaleDateString("en-KE", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
};

/**
 * Format a date string as "30 Mar 2026" — used in form date display fields.
 * Produces a zero-padded day with abbreviated month and 4-digit year.
 */
export const formatDisplayDate = (dateString: string): string => {
  if (!dateString) return "";
  const d = new Date(dateString);
  if (isNaN(d.getTime())) return dateString;
  const day = d.getDate().toString().padStart(2, "0");
  const month = d.toLocaleString("en-US", { month: "short" });
  const year = d.getFullYear();
  return `${day} ${month} ${year}`;
};

export function formatCurrency(
  amount: number,
  currency: string = "KES"
): string {
  const prefix = currency === "KES" ? "Ksh" : currency;

  return `${prefix} ${amount.toLocaleString("en-KE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export const money = (value: number | string | null | undefined): string =>
  value == null ? "—" : formatCurrency(Number(value), DEFAULT_CURRENCY);

/**
 * Mask an email address for display (e.g. "fra**********18@mail.com").
 */
export function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (local.length <= 5) {
    return `${local[0]}${"*".repeat(local.length - 1)}@${domain}`;
  }
  const start = local.slice(0, 3);
  const end = local.slice(-2);
  const masked = "*".repeat(Math.min(local.length - 5, 10));
  return `${start}${masked}${end}@${domain}`;
}

/**
 * Build the delta badge for a metric. A null/undefined delta means the
 * previous period was zero — the API deliberately returns null instead of
 * a fake percentage, so we render a dash rather than 0%.
 * `invert` flips sentiment for metrics where a decrease is good (expenses).
 */
export function formatDelta(
  value: number | null | undefined,
  { invert = false, suffix = "%" }: { invert?: boolean; suffix?: string } = {},
): MetricChange | null {
  if (value == null) return null;
  const sign = value > 0 ? "+" : "";
  const text = `${sign}${value.toFixed(1)}${suffix}`;
  if (value === 0) return { text, tone: "neutral" };
  const isGood = invert ? value < 0 : value > 0;
  return { text, tone: isGood ? "positive" : "negative" };
}

/**
 * Save a fetched Blob to the user's device under `filename`.
 *
 * Centralises the createObjectURL -> temporary anchor -> revokeObjectURL
 * dance so binary downloads (PDFs, attached documents) actually land on disk
 * instead of being fetched and discarded. The object URL is always
 * revoked, even if the click throws.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
