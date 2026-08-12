const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
export const API_URL = API_BASE_URL + "/api/v1/";

export const appConfig = {
  appName: import.meta.env.VITE_APP_NAME || "Business Central",
  appVersion: import.meta.env.VITE_APP_VERSION || "1.0.0",
  apiUrl: API_URL,
};

export const PAGINATION_DEFAULTS = {
  PER_PAGE_OPTIONS: [10, 20, 50, 100],
};

export const OTP_LENGTH = 6;
export const OTP_EXPIRY_SECONDS = 300; // 5 minutes

// COMPANY_INFO removed: the organisation identity is now
// data-driven via the owner profile API (services/ownerApi + DocumentOwnerHeader),
// snapshotted immutably onto issued documents. Do not reintroduce a hardcoded
// company constant.

export const COUNTRY_OPTIONS = [
  { value: "KE", label: "Kenya" },
  { value: "UG", label: "Uganda" },
  { value: "TZ", label: "Tanzania" },
];

export const DEFAULT_CURRENCY = "KES";

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
  petroleum_vat_13: 0.13,
  petroleum_vat_8: 0.08,
  vat_8: 0.08,
  vat_0: 0.0,
  no_tax: 0.0,
  exempt: 0.0,
};

export async function initTaxRates() {
  try {
    const res = await fetch(`${API_URL}taxes`);
    if (res.ok) {
      const data = await res.json();
      for (const key in TAX_RATES) {
        delete TAX_RATES[key];
      }
      Object.assign(TAX_RATES, data);
    }
  } catch (err) {
    console.error("Failed to fetch tax rates from API", err);
  }
}

export const TAX_CATEGORY_OPTIONS = [
  { value: "vat", label: "VAT" },
  { value: "exempt", label: "Exempt" },
  { value: "no_tax", label: "No Tax" },
] as const;


export const DEFAULT_DUE_DATE_DAYS = 30;

export const ACCEPTED_IMAGE_TYPES = ".jpeg,.jpg,.png,.gif,.webp" as const;
export const ACCEPTED_UPLOAD_TYPES = `${ACCEPTED_IMAGE_TYPES},.pdf,.doc,.docx,.xls,.xlsx,.txt,.csv` as const;
