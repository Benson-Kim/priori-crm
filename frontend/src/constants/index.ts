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
