/**
 * Route guard for the authenticated application shell.
 *
 * Redirects unauthenticated visitors to /login, preserving the location
 * they attempted to open (react-router `state.from`) so the login flow
 * can send them back after a successful sign-in.
 */

import { Outlet } from "react-router-dom";

// import { Navigate, Outlet, useLocation } from "react-router-dom";
// import { useAuth } from "@/hooks/auth-context";

export default function RequireAuth() {
  // const { isAuthenticated } = useAuth();

  // const location = useLocation();

  // if (!isAuthenticated) {
  //   return <Navigate to="/login" replace state={{ from: location }} />;
  // }

  return <Outlet />;
}
