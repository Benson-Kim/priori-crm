/**
 * SettingsLayout — the owner Settings section (issue #58 / ADR-0011).
 *
 * A real settings home for the organisation: business details + branding
 * (Organisation), document defaults (Documents) and the read-only module
 * entitlements granted by the platform operator (Modules, admin only).
 *
 * Access mirrors the backend: the owner profile writes require MANAGER or
 * ADMIN (useCanEditOwner), so members are bounced to the dashboard. The
 * Modules tab additionally requires ADMIN, matching the backend's
 * require_role(ADMIN) gate on GET /owner/modules.
 */

import { Navigate, NavLink, Outlet } from "react-router-dom";

import { useAuth, useCanEditOwner } from "@/hooks/auth-context";
import { cn } from "@/lib/utils";
import { ITEM_ACTIVE, ITEM_BASE, ITEM_IDLE } from "./sidebar-styles";



export default function SettingsLayout() {
  const { user } = useAuth();
  const canEdit = useCanEditOwner();

  const isAdmin = user?.role?.toLowerCase() === "admin";

  if (!canEdit) {
    return <Navigate to="/dashboard" replace />;
  }

  const tabs = [
    { path: "/settings/organisation", label: "Organisation" },
    { path: "/settings/documents", label: "Documents" },
    // Read-only entitlement view is admin-only (backend GET gate).
    ...(isAdmin ? [{ path: "/settings/modules", label: "Modules" }] : []),
  ];

  return (
    <div className="flex flex-col gap-6">
      <nav
        aria-label="Settings sections"
        className="bg-gray-100 flex w-fit items-center gap-1 rounded-lg border border-gray-200 overflow-hidden shrink-0 transition-all duration-200"
      >
        {tabs.map((tab) => (
          <NavLink
            key={tab.path}
            to={tab.path}
            className={({ isActive }) =>
              cn(ITEM_BASE, isActive ? ITEM_ACTIVE : ITEM_IDLE)
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
