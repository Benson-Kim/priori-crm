/**
 * Auth context + hook
 *
 * Kept separate from the AuthProvider component so the provider file exports
 * only a component (eslint react-refresh/only-export-components). The provider
 * lives in hooks/useAuth.tsx and supplies this context's value.
 */

import { createContext, useContext } from "react";

import type { AuthUser } from "@/services/authApi";

/**
 * Roles allowed to edit organisation-wide settings (the owner profile + logo).
 * Mirrors the backend's _WRITE_ROLES on the owner router (MANAGER/ADMIN), so
 * the UI only offers edit controls the backend will actually accept.
 */
const OWNER_WRITE_ROLES = new Set(["manager", "admin"]);

export interface AuthContextValue {
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  resendOtp: (email: string) => Promise<void>;
  verifyOtp: (email: string, code: string) => Promise<AuthUser>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | undefined>(
  undefined
);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}

/**
 * Whether the current user may edit the owner profile / logo
 * Read-only for everyone else, matching the backend authorisation.
 */
export function useCanEditOwner(): boolean {
  const { user } = useAuth();
  return user !== null && OWNER_WRITE_ROLES.has(user.role.toLowerCase());
}
