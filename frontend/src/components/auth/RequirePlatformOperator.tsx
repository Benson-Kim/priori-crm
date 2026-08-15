/**
 * Route guard for the platform-operator console (/platform).
 *
 * Mirrors the backend's require_role(PLATFORM_OPERATOR) gate on every
 * /platform route: only a signed-in user whose bootstrap role is
 * `platform_operator` may render the console; everyone else is bounced to
 * the tenant dashboard. The guard is a convenience only — the server-side
 * gate remains the enforcement point (ADR-0011).
 */

import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "@/hooks/auth-context";

export default function RequirePlatformOperator() {
  const { user } = useAuth();

  if (user?.role?.toLowerCase() !== "platform_operator") {
    return <Navigate to="/dashboard" replace />;
  }

  return <Outlet />;
}
