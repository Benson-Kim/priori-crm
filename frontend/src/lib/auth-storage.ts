/**
 * Token storage for authentication.
 *
 * Access and refresh tokens are persisted in localStorage and read by the
 * shared API client (lib/api.ts) to attach the Authorization header and to
 * refresh on 401 (LIB-FE-1, AUTH-FE-2).
 */

const ACCESS_TOKEN_KEY = "priori.accessToken";
const REFRESH_TOKEN_KEY = "priori.refreshToken";

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setTokens(accessToken: string, refreshToken?: string): void {
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    if (refreshToken !== undefined) {
      localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
    }
  } catch {
    /* storage unavailable (e.g. private mode) — ignore */
  }
}

export function setAccessToken(accessToken: string): void {
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  } catch {
    /* ignore */
  }
}

export function clearTokens(): void {
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}
