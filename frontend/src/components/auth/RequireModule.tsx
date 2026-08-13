/**
 * Layout-route guard for per-owner module entitlements.
 *
 * Rendered as a pathless layout element wrapping a module's route group:
 * when the module is disabled for the organisation the user is redirected
 * to /dashboard; otherwise the child routes render through <Outlet/>.
 *
 * Fails OPEN while the bootstrap is loading (useModuleEnabled treats a
 * missing map as enabled) so deep links never bounce during startup — the
 * backend's router-wide 403 gate remains the enforcement point.
 */

import { Navigate, Outlet } from "react-router-dom";

import { useModuleEnabled } from "@/hooks/useModuleAccess";

export default function RequireModule({ moduleKey }: { moduleKey: string }) {
  const enabled = useModuleEnabled(moduleKey);

  if (!enabled) {
    return <Navigate to="/dashboard" replace />;
  }

  return <Outlet />;
}
