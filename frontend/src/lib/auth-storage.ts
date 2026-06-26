/**
 * Token storage for authentication.
 *
 * Two persistence modes, controlled by the "Remember Me" checkbox:
 *
 *  1. **Session-only (default)** — tokens live in memory, mirrored to
 *     `sessionStorage` so a same-tab reload keeps working. Closing the browser
 *     ends the session.
 *
 *  2. **Remember-me** — tokens are persisted to `localStorage` so they survive
 *     browser restarts. The user stays signed in until they explicitly log out
 *     or the refresh token expires server-side.
 *
 * The shared API client (lib/api.ts) reads these to attach the Authorization
 * header and to refresh on 401.
 */

const ACCESS_TOKEN_KEY = "priori.accessToken";
const REFRESH_TOKEN_KEY = "priori.refreshToken";
const REMEMBER_ME_KEY = "priori.rememberMe";

// In-memory token state.
let accessToken: string | null = null;
let refreshToken: string | null = null;

function store(): Storage {
  try {
    return isRemembered() ? localStorage : sessionStorage;
  } catch {
    return sessionStorage;
  }
}

export function isRemembered(): boolean {
  try {
    return localStorage.getItem(REMEMBER_ME_KEY) === "1";
  } catch {
    return false;
  }
}

export function setRememberMe(enabled: boolean): void {
  try {
    if (enabled) {
      localStorage.setItem(REMEMBER_ME_KEY, "1");
    } else {
      localStorage.removeItem(REMEMBER_ME_KEY);
    }
  } catch {
    /* storage unavailable */
  }
}

function readPersistedAccessToken(): string | null {
  try {
    return store().getItem(ACCESS_TOKEN_KEY);
  } catch {
    return null;
  }
}

function readPersistedRefreshToken(): string | null {
  try {
    return store().getItem(REFRESH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getAccessToken(): string | null {
  if (accessToken !== null) {
    return accessToken;
  }
  // Recover from a reload (session or persistent store).
  accessToken = readPersistedAccessToken();
  return accessToken;
}

export function getRefreshToken(): string | null {
  if (refreshToken !== null) {
    return refreshToken;
  }
  // Recover from a reload when remember-me is active.
  refreshToken = readPersistedRefreshToken();
  return refreshToken;
}

export function setTokens(newAccessToken: string, newRefreshToken?: string): void {
  setAccessToken(newAccessToken);
  if (newRefreshToken !== undefined) {
    refreshToken = newRefreshToken;
    try {
      store().setItem(REFRESH_TOKEN_KEY, newRefreshToken);
    } catch {
      /* storage unavailable */
    }
  }
}

export function setAccessToken(newAccessToken: string): void {
  accessToken = newAccessToken;
  try {
    store().setItem(ACCESS_TOKEN_KEY, newAccessToken);
  } catch {
    /* storage unavailable (e.g. private mode) — memory copy still works */
  }
}

export function clearTokens(): void {
  accessToken = null;
  refreshToken = null;
  try {
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(REMEMBER_ME_KEY);
  } catch {
    /* ignore */
  }
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}
