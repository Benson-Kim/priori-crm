/**
 * Authentication provider
 *
 * Centralises the login → OTP → token flow so pages and the route guard share
 * one source of truth for "is the user signed in" and for the current user.
 * Tokens themselves are owned by lib/auth-storage (in-memory); this
 * provider only tracks the derived authentication state and the user profile.
 *
 * The context object and the useAuth hook live in hooks/auth-context.ts so
 * this file exports only a component (react-refresh/only-export-components).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { AuthContext, type AuthContextValue } from "@/hooks/auth-context";
import { setAuthFailureHandler } from "@/lib/api";
import { isAuthenticated as hasToken } from "@/lib/auth-storage";
import {
  login as loginRequest,
  resendOtp as resendOtpRequest,
  logout as logoutRequest,
  verifyOtp as verifyOtpRequest,
  type AuthUser,
} from "@/services/authApi";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authed, setAuthed] = useState<boolean>(hasToken());

  // Bridge unrecoverable 401s from the (non-React) API client into React.
  // Flipping auth state to false makes RequireAuth render a react-router
  // <Navigate to="/login"> on the next render — a client-side redirect with
  // NO full-page reload, so in-memory state and the bootstrap are preserved.
  useEffect(() => {
    setAuthFailureHandler(() => {
      setUser(null);
      setAuthed(false);
    });
    return () => setAuthFailureHandler(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    await loginRequest(email, password);
  }, []);

  const resendOtp = useCallback(async (email: string) => {
    await resendOtpRequest(email);
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
    () => ({ user, isAuthenticated: authed, login, resendOtp, verifyOtp, logout }),
    [user, authed, login, resendOtp, verifyOtp, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
