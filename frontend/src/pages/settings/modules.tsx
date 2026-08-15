/**
 * Settings › Modules — READ-ONLY per-owner module entitlements (ADR-0011).
 *
 * Entitlements are granted by the platform operator, not self-service:
 * the tenant-facing toggle endpoint was removed (issue #58 / QA finding
 * 09), so this screen only shows every module key with its effective
 * enabled state. Essential modules (auth, owner, health, dashboard) render
 * locked — they can never be disabled.
 */

import { Lock } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { Badge } from "@/components/ui/Badge";
import { LoadingState } from "@/components/ui/LoadingState";
import { useAuth } from "@/hooks/auth-context";
import { moduleLabel } from "@/lib/module-labels";
import {
  getModuleSettings,
  type ModuleSettingState,
} from "@/services/ownerApi";

export default function ModuleSettingsPage() {
  const { user } = useAuth();

  const [modules, setModules] = useState<ModuleSettingState[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  // Admin-only page: everyone else is bounced to the dashboard, matching
  // the backend's require_role(ADMIN) gate on GET /owner/modules.
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
          {/* One line, but the governance context has to stay: without it the
              read-only badges look like a bug rather than a policy. */}
          <p className="text-sm text-gray-500">
            Access is granted by your platform operator and cannot be changed
            here.
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
              <Badge variant={module.enabled ? "active" : "canceled"}>
                {module.enabled ? "Enabled" : "Disabled"}
              </Badge>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Essential modules
          </h2>

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
