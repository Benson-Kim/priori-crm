const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
export const API_URL = API_BASE_URL + "/api/v1/";

export const appConfig = {
  appName: import.meta.env.VITE_APP_NAME || "Priori CRM",
  appVersion: import.meta.env.VITE_APP_VERSION || "1.0.0",
  apiUrl: API_URL,
};

export const PAGINATION_DEFAULTS = {
  PER_PAGE_OPTIONS: [10, 20, 50, 100],
};

export const OTP_LENGTH = 6;
export const OTP_EXPIRY_SECONDS = 300; // 5 minutes

export const COMPANY_INFO = {
  name: "Priori Technologies",
  address: "P.O Box 124, 90600",
  phone: "+254712345678",
  email: "priori@techmail.com",
} as const;

export const CURRENCY_OPTIONS = [
  { value: "KES", label: "KES" },
  { value: "USD", label: "USD" },
  { value: "EUR", label: "EUR" },
  { value: "GBP", label: "GBP" },
] as const;

export type CurrencyOption = (typeof CURRENCY_OPTIONS)[number]["value"];

/**
 * Tax rate mappings
 */
export const TAX_RATES: Record<string, number> = {
  vat_16: 0.16,
  vat_8: 0.08,
  vat_0: 0.0,
  no_tax: 0.0,
  exempt: 0.0,
} as const;

export const TAX_CATEGORY_OPTIONS = [
  { value: "vat", label: "VAT" },
  { value: "exempt", label: "Exempt" },
  { value: "no_tax", label: "No Tax" },
] as const;

export const VAT_RATE_OPTIONS = [
  { value: "16", label: "16%" },
  { value: "8", label: "8%" },
  { value: "0", label: "0%" },
] as const;

export const DEFAULT_DUE_DATE_DAYS = 30;
