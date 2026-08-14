/**
 * Route guard for the authenticated application shell.
 *
 * Redirects unauthenticated visitors to /login, preserving the location
 * they attempted to open (react-router `state.from`) so the login flow
 * can send them back after a successful sign-in.
 *
 * Also mounts the OwnerProfileProvider for every authenticated route: the
 * per-owner module entitlements (`profile.enabledModules`) gate navigation
 * and routes in BOTH shells — the Business Central layout and the Sales
 * Desk layout — so the provider must sit above the point where the two
 * route trees split, not inside one of them.
 */

// import { Outlet } from "react-router-dom";

import { useAuth } from "@/hooks/auth-context";
import { OwnerProfileProvider } from "@/hooks/OwnerProfileProvider";
import { Navigate, Outlet, useLocation } from "react-router-dom";

export default function RequireAuth() {
  const { isAuthenticated } = useAuth();

  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return (
    <OwnerProfileProvider>
      <Outlet />
    </OwnerProfileProvider>
  );
}
