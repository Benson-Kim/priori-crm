/**
 * Authentication API.
 *
 * Wraps the backend auth endpoints and persists tokens via auth-storage so the
 * shared API client (lib/api.ts) can attach the bearer header and refresh on
 * 401. The login/OTP UI should call these helpers.
 *
 * Response types derive from the generated OpenAPI contract (`@/lib/apiTypes`)
 * rather than hand-mirrored interfaces.
 */

import { apiPost } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import { clearTokens, getRefreshToken, setTokens } from "@/lib/auth-storage";

export type AuthUser = Schema<"UserResponse">;

type MessageResponse = Schema<"MessageResponse">;
type TokenResponse = Schema<"TokenResponse">;

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
 * Resend the OTP verification code.
 */
export async function resendOtp(email: string): Promise<string> {
  const result = await apiPost<MessageResponse>("auth/resend-otp", {
    email,
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
