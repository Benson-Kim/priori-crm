/**
 * Top-level route guard
 *
 * Wraps the authenticated application shell. When there is no valid session
 * the user is redirected to /login, preserving the attempted location so we
 * can send them back after a successful sign-in.
 */

import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "@/hooks/auth-context";

export default function RequireAuth() {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
