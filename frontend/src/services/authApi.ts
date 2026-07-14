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

import { apiPost, apiPostPublic } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";
import { clearTokens, getRefreshToken, setTokens, setAuthUser } from "@/lib/auth-storage";

export type AuthUser = Schema<"UserResponse">;

type MessageResponse = Schema<"MessageResponse">;
type TokenResponse = Schema<"TokenResponse">;

/**
 * Step 1: validate credentials. The backend sends an OTP to the user's email
 * and returns a confirmation message (with the masked address).
 */
export async function login(email: string, password: string): Promise<string> {
  const result = await apiPostPublic<MessageResponse>("auth/login", {
    email,
    password,
  });
  return result.message;
}

/**
 * Resend the OTP verification code.
 */
export async function resendOtp(email: string): Promise<string> {
  const result = await apiPostPublic<MessageResponse>("auth/resend-otp", {
    email,
  });
  return result.message;
}

/**
 * Request a password-reset link. The backend always responds with the same
 * generic message regardless of whether the account exists (enumeration-safe),
 * so the caller should show that confirmation verbatim and never branch on it.
 */
export async function forgotPassword(email: string): Promise<string> {
  const result = await apiPostPublic<MessageResponse>("auth/forgot-password", {
    email,
  });
  return result.message;
}

/**
 * Complete a password reset with the emailed token and a new password.
 *
 * The endpoint issues no tokens (the user signs in normally afterwards) and
 * returns a generic 401 for any invalid / expired / used token. The field is
 * sent as `newPassword` to match the backend request alias.
 */
export async function resetPassword(
  token: string,
  newPassword: string
): Promise<string> {
  const result = await apiPostPublic<MessageResponse>("auth/reset-password", {
    token,
    newPassword,
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
  const result = await apiPostPublic<TokenResponse>("auth/verify-otp", {
    email,
    code,
  });
  setTokens(result.access_token, result.refresh_token);
  setAuthUser(result.user);
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
