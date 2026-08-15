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

/** Pluralise a count against its noun: "1 deal", "4 deals". */
export const plural = (count: number, noun: string) =>
  `${count} ${noun}${count === 1 ? "" : "s"}`;

/**
 * Avatar initials for a display name, capped at two characters — "St. John
 * Paul II Sabbatical Centre" renders "SC", not "SJPISC". First + last word
 * mirrors the backend's `Vendor.display_initials`, which is the one place the
 * two conventions sit side by side (the vendor detail card prefers the
 * server's value and falls back to this). `avatarInitials` in
 * components/ui/avatar-utils.ts is the table-avatar equivalent and takes the
 * first two words instead; leave that one alone.
 */
export const getNameInitials = (name: string) => {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
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

/**
 * The single date-formatting helper (#53 ratified rule): `D MMM YYYY` in
 * cards/chips (the default), `YYYY-MM-DD` in tables and logs. Rendering all
 * dates through one helper is what prevents the off-by-one and mixed-format
 * drift the design exports showed.
 */
export const formatDate = (
  dateString: string,
  style: "chip" | "table" = "chip"
) => {
  if (!dateString) return "";
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return dateString;
  if (style === "table") {
    // UTC getters so an ISO timestamp never renders as the previous local day.
    const month = String(date.getUTCMonth() + 1).padStart(2, "0");
    const day = String(date.getUTCDate()).padStart(2, "0");
    return `${date.getUTCFullYear()}-${month}-${day}`;
  }
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
 * Account password policy — the single client-side source of truth, kept in
 * lockstep with the backend `validate_password_strength`
 * (api/app/common/validators.py). Tighten in one place if the rules change.
 */
export const PASSWORD_POLICY = {
  MIN_LENGTH: 8,
  MAX_LENGTH: 128,
} as const;

/**
 * Validate a new password against the shared policy: 8-128 characters, at
 * least one letter and at least one digit. Returns an error message string, or
 * null when the password is acceptable. This is a UX pre-check only; the
 * backend remains the authority and rejects anything non-conforming.
 */
export function validatePasswordStrength(password: string): string | null {
  if (password.length < PASSWORD_POLICY.MIN_LENGTH) {
    return `Password must be at least ${PASSWORD_POLICY.MIN_LENGTH} characters long.`;
  }
  if (password.length > PASSWORD_POLICY.MAX_LENGTH) {
    return `Password must be at most ${PASSWORD_POLICY.MAX_LENGTH} characters long.`;
  }
  if (!/[A-Za-z]/.test(password)) {
    return "Password must contain at least one letter.";
  }
  if (!/\d/.test(password)) {
    return "Password must contain at least one number.";
  }
  return null;
}

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
 * Format a Kenyan phone number input into a readable format.
 * Example: "254712345678" => "712 345 678" or "0712345678" => "712 345 678"
 */
const PHONE_MASK = "7XX XXX XXX";

const getKenyanPhoneDigits = (value: string) => {
  let digits = value.replace(/\D/g, "");

  if (digits.startsWith("254")) {
    digits = digits.slice(3);
  }

  if (digits.startsWith("0")) {
    digits = digits.slice(1);
  }

  return digits.slice(0, 9);
};

export function formatKenyanPhoneInput(value: string) {
  const digits = getKenyanPhoneDigits(value);

  const part1 = digits.slice(0, 3);
  const part2 = digits.slice(3, 6);
  const part3 = digits.slice(6, 9);

  return [part1, part2, part3].filter(Boolean).join(" ");
};

export function getKenyanPhoneGhostText(value: string) {
  const digits = getKenyanPhoneDigits(value);

  let digitCount = 0;

  for (let index = 0; index < PHONE_MASK.length; index += 1) {
    if (PHONE_MASK[index] !== " ") {
      digitCount += 1;
    }

    if (digitCount === digits.length) {
      return PHONE_MASK.slice(index + 1);
    }
  }

  return "";
};

/**
 * Returns the digits of a Kenyan phone number.
 *
 * @param value The phone number to extract digits from.
 * @returns The digits of the phone number.
 */

export function normalizeKenyanPhoneNumber(value: string) {
  if (!value) return "";

  const trimmed = value.trim();
  if (!trimmed) return "";

  if (/^\+/.test(trimmed)) {
    return trimmed;
  }

  const digits = trimmed.replace(/\D/g, "");
  if (!digits) return "";

  if (digits.startsWith("254")) {
    return `+${digits.slice(0, 12)}`;
  }

  if (digits.startsWith("0") && digits.length <= 10) {
    return `+254${digits.slice(1, 10)}`;
  }

  return trimmed;
};

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

export function responseFilename(
  contentDisposition: string | null
): string | undefined {
  if (!contentDisposition) return undefined;
  const encoded = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    const value = encoded.replace(/^"|"$/g, "");
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  }
  return contentDisposition.match(/filename="?([^";]+)"?/i)?.[1];
}

export function chooseDownloadFilename(
  serverFilename: string | undefined,
  fallback: string
): string {
  return serverFilename?.trim() || fallback;
}