/**
 * Module Settings — admin-only per-owner module entitlements (feature toggles).
 *
 * Lists every module key with its effective enabled state. Toggleable
 * modules can be switched on/off (PATCH /owner/modules/{key}); essential
 * modules (auth, owner, health, dashboard) render locked and cannot be
 * disabled — the backend rejects such attempts with a 422. After a toggle
 * the owner-profile bootstrap is refreshed so the sidebar and route guards
 * pick up the change immediately.
 */

import { Lock } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { LoadingState } from "@/components/ui/LoadingState";
import { Toggle } from "@/components/ui/Toggle";
import { useAuth } from "@/hooks/auth-context";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { ApiError } from "@/lib/api";
import {
  getModuleSettings,
  updateModuleSetting,
  type ModuleSettingState,
} from "@/services/ownerApi";

/** Human labels for the known module keys (fallback: prettified key). */
const MODULE_LABELS: Record<string, string> = {
  customers: "Customers",
  quotes: "Quotes",
  invoices: "Invoices",
  deals: "Deals",
  nurture: "Nurture (Future Pipeline)",
  onboarding: "Onboarding",
  vendors: "Vendors",
  expenses: "Expenses",
  purchase_orders: "Purchase Orders",
  statements: "Statements",
  reports: "Reports",
  sales_desk: "Sales Desk",
  auth: "Authentication",
  owner: "Organisation Profile",
  health: "Health",
  dashboard: "Dashboard",
};

function moduleLabel(key: string): string {
  return (
    MODULE_LABELS[key] ??
    key
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

export default function ModuleSettingsPage() {
  const { user } = useAuth();
  const { refresh } = useOwnerProfile();

  const [modules, setModules] = useState<ModuleSettingState[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const isAdmin = user?.role?.toLowerCase() === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getModuleSettings();
      setModules(response.modules);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load module settings"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) {
      void load();
    }
  }, [isAdmin, load]);

  const handleToggle = useCallback(
    async (moduleKey: string, enabled: boolean) => {
      setSavingKey(moduleKey);
      setError(null);
      try {
        const updated = await updateModuleSetting(moduleKey, enabled);
        setModules((prev) =>
          prev
            ? prev.map((m) => (m.moduleKey === moduleKey ? updated : m))
            : prev
        );
        // Refresh the bootstrap so nav visibility and route guards follow.
        await refresh();
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to update the module setting"
        );
      } finally {
        setSavingKey(null);
      }
    },
    [refresh]
  );

  // Admin-only page: everyone else is bounced to the dashboard, matching
  // the backend's require_role(ADMIN) gate on GET/PATCH /owner/modules.
  if (!isAdmin) {
    return <Navigate to="/dashboard" replace />;
  }

  if (loading && modules === null) {
    return <LoadingState message="Loading module settings..." />;
  }

  const toggleable = modules?.filter((m) => !m.essential) ?? [];
  const essential = modules?.filter((m) => m.essential) ?? [];

  return (
    <div className="max-w-3xl flex flex-col gap-8">
      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Modules</h2>
          <p className="text-sm text-gray-500">
            Disabled modules disappear from navigation and their pages and
            APIs are blocked for every user in the organisation.
          </p>
        </div>
        <ul className="divide-y divide-gray-200 rounded-xl border border-gray-200 bg-white">
          {toggleable.map((module) => (
            <li
              key={module.moduleKey}
              className="flex items-center justify-between gap-4 px-4 py-3"
            >
              <span className="text-sm font-medium text-gray-800">
                {moduleLabel(module.moduleKey)}
              </span>
              <Toggle
                checked={module.enabled}
                disabled={savingKey === module.moduleKey}
                onChange={(checked) =>
                  void handleToggle(module.moduleKey, checked)
                }
                aria-label={`Toggle ${moduleLabel(module.moduleKey)}`}
              />
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Essential modules
          </h2>
          <p className="text-sm text-gray-500">
            Core infrastructure that is always enabled and cannot be turned
            off.
          </p>
        </div>
        <ul className="divide-y divide-gray-200 rounded-xl border border-gray-200 bg-white">
          {essential.map((module) => (
            <li
              key={module.moduleKey}
              className="flex items-center justify-between gap-4 px-4 py-3"
            >
              <span className="text-sm font-medium text-gray-800">
                {moduleLabel(module.moduleKey)}
              </span>
              <span
                className="flex items-center gap-1.5 text-xs font-medium text-gray-400"
                title="Essential module — always enabled"
              >
                <Lock size={14} aria-hidden="true" />
                Always on
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
