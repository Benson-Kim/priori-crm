/**
 * Base API client for all HTTP communication.
 *
 * Every request carries the bearer access token. On a 401 the client attempts
 * a single token refresh via /auth/refresh and retries the original request;
 * if the refresh fails the tokens are cleared and the user is sent to login
 */

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from "@/lib/auth-storage";
import { appConfig } from "@/lib/constants";
import type { PaginatedApiResponse } from "@/lib/types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>
): string {
  if (!appConfig.apiUrl || appConfig.apiUrl.startsWith("undefined")) {
    throw new ApiError("Server is not responding", 0);
  }
  const url = new URL(path, appConfig.apiUrl);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
  }
  return url.toString();
}

function withAuthHeaders(headers: HeadersInit = {}): Headers {
  const result = new Headers(headers);
  const token = getAccessToken();
  if (token) {
    result.set("Authorization", `Bearer ${token}`);
  }
  return result;
}

// Network-level guardrails shared by every request.
const REQUEST_TIMEOUT_MS = 20_000;
const DOWNLOAD_TIMEOUT_MS = 120_000;
const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const MAX_RETRY_AFTER_MS = 10_000;

/**
 * fetch wrapper that enforces a timeout and converts transient transport
 * failures (DNS blip, aborted request, CORS rejection) into a typed
 * ApiError(status=0) instead of an opaque TypeError. Network errors surface
 * with the same shape as the buildUrl guard ("Server is not responding").
 *
 * `timeoutMs` lets slow, legitimate endpoints (e.g. report generation) opt
 * into a longer budget than the default request timeout.
 */
async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("The request timed out. Please try again.", 0);
    }
    throw new ApiError("Server is not responding", 0);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Parse a Retry-After header (seconds, or an HTTP date) into milliseconds.
 * Returns null if the header is absent or unparseable.
 */
function parseRetryAfterMs(response: Response): number | null {
  const header = response.headers.get("Retry-After");
  if (!header) {
    return null;
  }

  const seconds = Number(header);
  if (!Number.isNaN(seconds)) {
    return Math.max(0, seconds * 1000);
  }

  const dateMs = Date.parse(header);
  if (!Number.isNaN(dateMs)) {
    return Math.max(0, dateMs - Date.now());
  }

  return null;
}

// Single-flight refresh: concurrent 401s share one refresh request.
let refreshPromise: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return false;
  }

  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const response = await fetch(buildUrl("auth/refresh"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) {
          return false;
        }
        const data = await response.json().catch(() => null);
        if (!data?.access_token) {
          return false;
        }
        // /auth/refresh rotates the refresh token: the presented
        // token is revoked server-side, so persist the new pair or the next
        // refresh will be rejected.
        setTokens(data.access_token, data.refresh_token);
        return true;
      } catch {
        return false;
      } finally {
        refreshPromise = null;
      }
    })();
  }

  return refreshPromise;
}

function redirectToLogin(): void {
  clearTokens();
  if (typeof window !== "undefined" && window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Options controlling authedFetch's retry behavior. The two concerns are
 * tracked independently so that, e.g., a transport-failure retry on a GET
 * doesn't also disable the 401 -> refresh -> retry path on that retry: a
 * request can legitimately need both a transport retry AND a token refresh
 * in the same lifecycle.
 */
interface AuthedFetchOptions {
  /** Per-request timeout override (defaults to REQUEST_TIMEOUT_MS). */
  timeoutMs?: number;
  /** Whether a transient-failure / retryable-5xx retry is still allowed. */
  retryAllowed?: boolean;
  /** Whether a 401 -> refresh -> retry attempt is still allowed. */
  refreshAllowed?: boolean;
}

/**
 * Perform a fetch with bearer auth and a one-time 401 -> refresh -> retry.
 *
 * Idempotent requests (GET / HEAD) additionally get a single backoff retry
 * on a transient transport failure or a 502/503/504 — never for mutating
 * methods, which are unsafe to replay. The transport/5xx retry budget and
 * the 401-refresh budget are tracked separately, so a request that needs
 * both (e.g. a flaky network on a request whose token also happens to have
 * expired) still gets refreshed rather than being bounced to login.
 */
async function authedFetch(
  url: string,
  init: RequestInit,
  options: AuthedFetchOptions = {}
): Promise<Response> {
  const {
    timeoutMs,
    retryAllowed = true,
    refreshAllowed = true,
  } = options;

  const method = (init.method ?? "GET").toUpperCase();
  const isIdempotent = method === "GET" || method === "HEAD";

  let response: Response;
  try {
    response = await fetchWithTimeout(
      url,
      { ...init, headers: withAuthHeaders(init.headers) },
      timeoutMs
    );
  } catch (err) {
    // Transient transport failure: retry once for idempotent requests only.
    // refreshAllowed is preserved untouched so a 401 on the retry still
    // gets a chance to refresh instead of bouncing straight to login.
    if (isIdempotent && retryAllowed) {
      await delay(300);
      return authedFetch(url, init, {
        timeoutMs,
        retryAllowed: false,
        refreshAllowed,
      });
    }
    throw err;
  }

  if (response.status === 401 && refreshAllowed) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      // The retry-after-refresh is a fresh attempt at the original request;
      // a transient transport failure on it is still worth one retry, but
      // we never refresh twice for the same call.
      return authedFetch(url, init, {
        timeoutMs,
        retryAllowed,
        refreshAllowed: false,
      });
    }
    redirectToLogin();
    return response;
  }

  // Retry once on a transient upstream error for idempotent requests. If the
  // server tells us how long to wait (Retry-After — e.g. an export queue at
  // capacity), honor that instead of hammering it again after 300ms, and
  // skip the retry entirely if the wait would be unreasonably long.
  if (isIdempotent && retryAllowed && RETRYABLE_STATUSES.has(response.status)) {
    const retryAfterMs = parseRetryAfterMs(response);
    if (retryAfterMs === null) {
      await delay(300);
    } else if (retryAfterMs <= MAX_RETRY_AFTER_MS) {
      await delay(retryAfterMs);
    } else {
      // Server asked us to back off longer than we're willing to block the
      // caller for; surface the response as-is rather than retrying.
      return response;
    }
    return authedFetch(url, init, {
      timeoutMs,
      retryAllowed: false,
      refreshAllowed,
    });
  }

  return response;
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));

    // Handle validation errors with detailed information
    if (body.details?.errors && Array.isArray(body.details.errors)) {
      const errorMessages = body.details.errors
        .map((err: { loc?: (string | number)[]; msg?: string }) => {
          const field = err.loc?.join(".") || "unknown";
          return `${field}: ${err.msg}`;
        })
        .join("; ");
      throw new ApiError(
        errorMessages || body.error || "Validation failed",
        response.status
      );
    }

    const message =
      body.detail ||
      body.error ||
      response.statusText ||
      "An unexpected error occurred";
    throw new ApiError(message, response.status);
  }

  // 204 No Content — return empty object
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>
): Promise<T> {
  const response = await authedFetch(buildUrl(path, params), { method: "GET" });
  return handleResponse<T>(response);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await authedFetch(buildUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(response);
}

export async function apiPostPublic<T>(path: string, body: unknown): Promise<T> {
  try {
    const response = await fetch(buildUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await handleResponse<T>(response);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new ApiError(
        "Network error: Unable to reach the server. Please check your connection or try again.",
        0
      );
    }
    throw error;
  }
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const response = await authedFetch(buildUrl(path), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(response);
}

export async function apiDelete<T>(path: string): Promise<T> {
  const response = await authedFetch(buildUrl(path), { method: "DELETE" });
  return handleResponse<T>(response);
}

/**
 * Upload one or more files (and optional fields) as multipart/form-data
 * through the shared client so the request carries auth and refresh on 401.
 * Do NOT set Content-Type manually — the browser sets
 * the multipart boundary.
 */
export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const response = await authedFetch(buildUrl(path), {
    method: "POST",
    body: formData,
  });
  return handleResponse<T>(response);
}

/**
 * Multipart PUT variant of apiUpload (e.g. replacing the owner logo) — same
 * shared-client auth/refresh guarantees; the browser sets the boundary.
 */
export async function apiUploadPut<T>(path: string, formData: FormData): Promise<T> {
  const response = await authedFetch(buildUrl(path), {
    method: "PUT",
    body: formData,
  });
  return handleResponse<T>(response);
}

/**
 * Download a binary resource (PDF, Excel, document) through the shared client
 * so it carries auth and refresh on 401. Uses DOWNLOAD_TIMEOUT_MS rather than
 * the default request budget since report/export generation on the backend
 * can legitimately run far longer than a normal API call before it starts
 * streaming a response.
 */
export async function apiDownload(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>
): Promise<Blob> {
  const response = await authedFetch(
    buildUrl(path, params),
    { method: "GET" },
    { timeoutMs: DOWNLOAD_TIMEOUT_MS }
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      body.detail || body.error || response.statusText || "Download failed",
      response.status
    );
  }
  return response.blob();
}

export function flattenPaginated<T>(raw: PaginatedApiResponse<T>) {
  return {
    items: raw.items,
    total: raw.metadata.total ?? 0,
    page: raw.metadata.page,
    per_page: raw.metadata.per_page,
    total_pages: raw.metadata.total_pages ?? 1,
  };
}
