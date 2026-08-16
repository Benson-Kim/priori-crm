/**
 * Base API client for all HTTP communication.
 *
 * Every request carries the bearer access token. On a 401 the client attempts
 * a single token refresh via /auth/refresh and retries the original request;
 * if the refresh fails the tokens are cleared and the user is sent to login.
 *
 * One 401 is NOT a stale access token: `STEP_UP_REQUIRED` (issue #67) means
 * the access-control policy wants a fresh OTP for this particular action —
 * sensitive work in the middle of the night, or a session whose risk score
 * crossed a threshold. Rotating tokens cannot satisfy that, so the client
 * must route to the login → OTP flow instead of refreshing. Treating it as
 * an ordinary 401 burns a refresh rotation per request and leaves the user
 * staring at "please sign in again" while signing in again changes nothing.
 */

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from "@/lib/auth-storage";
import { appConfig } from "@/lib/constants";
import type { PaginatedApiResponse } from "@/lib/types";
import { responseFilename } from "@/lib/utils";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * One entry of a 422 response's `details.errors`.
 *
 * `field` and `message` are the user-facing pair the API emits; `loc` and
 * `msg` are the underlying Pydantic values, kept for debugging and never
 * shown to a user.
 */
export interface ValidationErrorDetail {
  field?: string;
  message?: string;
  loc?: (string | number)[];
  msg?: string;
}

const GENERIC_VALIDATION_MESSAGE =
  "Please check the information you entered and try again.";

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>
): string {
  if (!appConfig.apiUrl || appConfig.apiUrl.startsWith("undefined")) {
    throw new ApiError("Server is not responding", 0);
  }
  /*
   * `apiUrl` carries the version prefix ("/api/v1/"), and `new URL` treats a
   * path beginning with "/" as absolute, which would silently drop that
   * prefix and send every request to the host root. Callers write endpoint
   * paths either way, so the leading slash is trimmed rather than trusted.
   */
  const url = new URL(path.replace(/^\/+/, ""), appConfig.apiUrl);
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

/**
 * Optional auth-failure handler, registered by the React layer (AuthProvider).
 *
 * The API client is a plain module and cannot call react-router directly. When
 * a 401 cannot be recovered, it invokes this handler so the app can clear auth
 * state and navigate to /login WITHOUT a full-page reload. A hard reload wipes
 * in-memory state and re-runs the whole bootstrap, which is exactly the race
 * that caused the blank screen / login flash.
 */
type AuthFailureHandler = () => void;
let authFailureHandler: AuthFailureHandler | null = null;

export function setAuthFailureHandler(handler: AuthFailureHandler | null): void {
  authFailureHandler = handler;
}

function redirectToLogin(): void {
  clearTokens();

  // Preferred path: let the React layer navigate via react-router (no reload).
  if (authFailureHandler) {
    authFailureHandler();
    return;
  }

  // Fallback for non-React / early-boot contexts where no handler is set yet.
  if (typeof window !== "undefined" && window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

/**
 * Whether a 401 is a policy step-up challenge rather than an expired token.
 *
 * Reads a CLONE of the response: the body is a one-shot stream and the
 * caller still needs it to render the API's explanation.
 */
async function isStepUpRequired(response: Response): Promise<boolean> {
  try {
    const body = await response.clone().json();
    return body?.error_code === "STEP_UP_REQUIRED";
  } catch {
    // Non-JSON or already-consumed body: fall back to the ordinary 401 path.
    return false;
  }
}


/**
 * Perform a fetch with bearer auth and a one-time 401 -> refresh -> retry.
 */
async function authedFetch(
  url: string,
  init: RequestInit,
  allowRetry = true
): Promise<Response> {
  const isIdempotent = !init.method || ["GET", "HEAD", "OPTIONS"].includes(init.method.toUpperCase());
  const maxRetries = isIdempotent ? 2 : 0;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(url, {
        ...init,
        headers: withAuthHeaders(init.headers),
      });

      if (response.status === 401) {
        // A step-up challenge is answered by a fresh OTP, never by a token
        // rotation — go straight to the login flow (which already carries
        // the intended destination through to the OTP step via router
        // state, so the user lands back where they were) and keep the
        // response body so the caller can explain why.
        if (await isStepUpRequired(response)) {
          redirectToLogin();
          return response;
        }

        if (allowRetry) {
          const refreshed = await refreshAccessToken();
          if (refreshed) {
            return authedFetch(url, init, false);
          }
          redirectToLogin();
        }
      }

      return response;
    } catch (error) {
      if (error instanceof TypeError && attempt < maxRetries) {
        // NetworkError or CORS error; retry after a delay
        await new Promise((res) => setTimeout(res, 1000 * (attempt + 1)));
        continue;
      }
      if (error instanceof TypeError) {
        throw new ApiError(
          "Network error: Unable to reach the server. Please check your connection or try again.",
          0
        );
      }
      throw error;
    }
  }
  throw new ApiError("Unexpected fetch failure", 0);
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));

    // Validation errors: the API humanizes each entry into a `message`
    // written for end users. Render those, never the raw Pydantic `loc`/`msg`
    // (which produced "body.password: string should have at least 8
    // characters" on the login screen).
    if (body.details?.errors && Array.isArray(body.details.errors)) {
      const friendly = (body.details.errors as ValidationErrorDetail[])
        .map((err) => err.message)
        .filter((msg): msg is string => typeof msg === "string" && msg.length > 0);

      if (friendly.length > 0) {
        throw new ApiError(friendly.join(" "), response.status);
      }
      // An older API build without humanized messages: fall back to the
      // envelope summary rather than reconstructing text from `loc`/`msg`.
      throw new ApiError(
        typeof body.error === "string" && body.error
          ? body.error
          : GENERIC_VALIDATION_MESSAGE,
        response.status
      );
    }

    // `detail` is a string for hand-raised HTTPExceptions but an array for any
    // 422 that bypasses the API's handler; only render it when it is a string,
    // otherwise it stringifies to "[object Object]".
    const message =
      (typeof body.detail === "string" && body.detail) ||
      (typeof body.error === "string" && body.error) ||
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

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const response = await authedFetch(buildUrl(path), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(response);
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

export interface DownloadResult {
  blob: Blob;
  filename?: string;
}

export async function apiDownloadWithFilename(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>
): Promise<DownloadResult> {
  const response = await authedFetch(buildUrl(path, params), { method: "GET" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      (typeof body.detail === "string" && body.detail) ||
        (typeof body.error === "string" && body.error) ||
        response.statusText ||
        "Download failed",
      response.status
    );
  }
  const blob = await response.blob();
  const contentDisposition = response.headers.get("Content-Disposition");
  return { blob, filename: responseFilename(contentDisposition) };
}

/**
 * Download a binary resource (PDF, Excel, document) through the shared client
 * so it carries auth and refresh on 401.
 */
export async function apiDownload(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>
): Promise<Blob> {
  const { blob } = await apiDownloadWithFilename(path, params);
  return blob;
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
