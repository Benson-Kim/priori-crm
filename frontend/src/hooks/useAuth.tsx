/**
 * Authentication provider (ISSUE-036 / ISSUE-030).
 *
 * Centralises the login → OTP → token flow so pages and the route guard share
 * one source of truth for "is the user signed in" and for the current user.
 * Tokens themselves are owned by lib/auth-storage (in-memory, ISSUE-031); this
 * provider only tracks the derived authentication state and the user profile.
 *
 * The context object and the useAuth hook live in hooks/auth-context.ts so
 * this file exports only a component (react-refresh/only-export-components).
 */

import {
  useCallback,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  login as loginRequest,
  logout as logoutRequest,
  verifyOtp as verifyOtpRequest,
  type AuthUser,
} from "@/services/authApi";
import { isAuthenticated as hasToken } from "@/lib/auth-storage";
import { AuthContext, type AuthContextValue } from "@/hooks/auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authed, setAuthed] = useState<boolean>(hasToken());

  const login = useCallback(async (email: string, password: string) => {
    await loginRequest(email, password);
  }, []);

  const verifyOtp = useCallback(async (email: string, code: string) => {
    const verifiedUser = await verifyOtpRequest(email, code);
    setUser(verifiedUser);
    setAuthed(true);
    return verifiedUser;
  }, []);

  const logout = useCallback(() => {
    logoutRequest();
    setUser(null);
    setAuthed(false);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, isAuthenticated: authed, login, verifyOtp, logout }),
    [user, authed, login, verifyOtp, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
