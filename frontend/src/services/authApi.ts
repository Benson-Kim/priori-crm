/**
 * Authentication API.
 *
 * Wraps the backend auth endpoints and persists tokens via auth-storage so the
 * shared API client (lib/api.ts) can attach the bearer header and refresh on
 * 401. The login/OTP UI should call these helpers.
 */

import { apiPost } from "@/lib/api";
import { clearTokens, getRefreshToken, setTokens } from "@/lib/auth-storage";

export interface AuthUser {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  avatar_url: string | null;
  role: string;
}

interface MessageResponse {
  message: string;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

/**
 * Step 1: validate credentials. The backend sends an OTP to the user's email
 * and returns a confirmation message (with the masked address).
 */
export async function login(email: string, password: string): Promise<string> {
  const result = await apiPost<MessageResponse>("auth/login", {
    email,
    password,
  });
  return result.message;
}

/**
 * Step 2: verify the OTP. On success the access and refresh tokens are
 * persisted so every subsequent request carries the bearer header.
 */
export async function verifyOtp(
  email: string,
  code: string
): Promise<AuthUser> {
  const result = await apiPost<TokenResponse>("auth/verify-otp", {
    email,
    code,
  });
  setTokens(result.access_token, result.refresh_token);
  return result.user;
}

/**
 * Log out: revoke the refresh token server-side then clear the
 * in-memory tokens. Revocation is best-effort — the local session is cleared
 * regardless so a backend/network failure never traps the user signed in.
 */
export function logout(): void {
  const refreshToken = getRefreshToken();
  if (refreshToken) {
    // Fire-and-forget; the endpoint is idempotent and tolerates a bad token.
    void apiPost<MessageResponse>("auth/logout", {
      refresh_token: refreshToken,
    }).catch(() => {
      /* ignore: local sign-out below is what matters */
    });
  }
  clearTokens();
}
