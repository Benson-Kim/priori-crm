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

const TAB_BASE =
  "rounded-lg px-4 py-2 text-sm font-medium transition-colors";
const TAB_ACTIVE = "bg-white text-gray-900 shadow-sm";
const TAB_IDLE = "text-gray-500 hover:text-gray-900";

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
        className="flex w-fit items-center gap-1 rounded-xl bg-gray-100 p-1"
      >
        {tabs.map((tab) => (
          <NavLink
            key={tab.path}
            to={tab.path}
            className={({ isActive }) =>
              cn(TAB_BASE, isActive ? TAB_ACTIVE : TAB_IDLE)
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
