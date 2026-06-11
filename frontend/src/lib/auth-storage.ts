/**
 * Token storage for authentication.
 *
 * Tokens are deliberately NOT persisted in localStorage, where they would be
 * exfiltratable by any XSS. Instead:
 *  - the access token lives in module memory (and is mirrored to sessionStorage
 *    only so a same-tab reload during an active session keeps working; it is
 *    cleared on logout and is far shorter-lived than a refresh token);
 *  - the refresh token lives in memory ONLY and is never written to web
 *    storage. The long-term goal is to move it to an HttpOnly, Secure cookie
 *    set by the backend; until then keeping it out of storage already removes
 *    the persistent XSS-theft target.
 *
 * The shared API client (lib/api.ts) reads these to attach the Authorization
 * header and to refresh on 401.
 */

const ACCESS_TOKEN_KEY = "priori.accessToken";

// In-memory token state. Cleared on logout and lost on a full reload.
let accessToken: string | null = null;
let refreshToken: string | null = null;

function readSessionAccessToken(): string | null {
  try {
    return sessionStorage.getItem(ACCESS_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getAccessToken(): string | null {
  if (accessToken !== null) {
    return accessToken;
  }
  // Recover a same-tab reload within an active session.
  accessToken = readSessionAccessToken();
  return accessToken;
}

export function getRefreshToken(): string | null {
  return refreshToken;
}

export function setTokens(newAccessToken: string, newRefreshToken?: string): void {
  setAccessToken(newAccessToken);
  if (newRefreshToken !== undefined) {
    refreshToken = newRefreshToken;
  }
}

export function setAccessToken(newAccessToken: string): void {
  accessToken = newAccessToken;
  try {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, newAccessToken);
  } catch {
    /* storage unavailable (e.g. private mode) — memory copy still works */
  }
}

export function clearTokens(): void {
  accessToken = null;
  refreshToken = null;
  try {
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}
