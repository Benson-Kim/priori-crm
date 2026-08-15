/**
 * Platform-operator console (issue #62 Phase B, ADR-0011).
 *
 * Owner list from GET /platform/owners; per-owner entitlement table from
 * GET /platform/owners/{owner_id}/modules; toggles call the audited PATCH.
 *
 * Behaviour rules (from the issue's consolidation review):
 * - PESSIMISTIC updates: the toggle state is never flipped locally — the
 *   PATCH resolves, then the whole table is refetched, so the screen always
 *   shows the server's resolved state.
 * - Revoking a module is destructive to tenant workflows, so a confirmation
 *   dialog names the owner AND the module before any disable PATCH.
 * - Essential modules render locked with the backend's 422 rationale as a
 *   hint — the API refuses to disable them.
 * - Empty/error states always render explanatory copy with a retry action;
 *   an unreachable API or zero owners must never look like a blank panel
 *   (the !56 Settings wipe-bug lesson).
 */

import { Lock, RefreshCw, ServerOff, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { LoadingState } from "@/components/ui/LoadingState";
import { Toggle } from "@/components/ui/Toggle";
import { moduleLabel } from "@/lib/module-labels";
import {
  getOwnerModuleSettings,
  listPlatformOwners,
  setOwnerModuleEnabled,
  type ModuleSettingState,
  type PlatformOwnerSummary,
} from "@/services/platformApi";

const ESSENTIAL_HINT =
  "Essential module (auth, organisation profile, health, dashboard) — always enabled; the platform API refuses to disable it (422).";

/** Explanatory error panel with a retry action — never a blank screen. */
function ErrorPanel({
  title,
  detail,
  onRetry,
}: Readonly<{ title: string; detail: string; onRetry: () => void }>) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-6 py-10 text-center">
      <ServerOff size={28} className="text-red-400" aria-hidden="true" />
      <div>
        <p className="text-sm font-semibold text-red-700">{title}</p>
        <p className="mt-1 text-sm text-red-600">{detail}</p>
      </div>
      <Button
        type="button"
        variant="outline-danger"
        size="sm"
        className="text-sm"
        onClick={onRetry}
      >
        <RefreshCw size={14} aria-hidden="true" />
        Try again
      </Button>
    </div>
  );
}

export default function PlatformConsolePage() {
  const [owners, setOwners] = useState<PlatformOwnerSummary[] | null>(null);
  const [ownersError, setOwnersError] = useState<string | null>(null);
  const [ownersLoading, setOwnersLoading] = useState(true);

  const [selectedOwnerId, setSelectedOwnerId] = useState<string | null>(null);

  const [modules, setModules] = useState<ModuleSettingState[] | null>(null);
  const [modulesError, setModulesError] = useState<string | null>(null);
  const [modulesLoading, setModulesLoading] = useState(false);

  // Module key with an in-flight PATCH (disables every toggle meanwhile).
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Pending revoke awaiting explicit confirmation (never enable — only
  // disabling is destructive to tenant workflows).
  const [pendingRevoke, setPendingRevoke] = useState<ModuleSettingState | null>(
    null
  );

  const loadOwners = useCallback(async () => {
    setOwnersLoading(true);
    setOwnersError(null);
    try {
      const response = await listPlatformOwners();
      setOwners(response.owners);
      // Single-tenant deployments hold exactly one owner profile today:
      // select it immediately so the console is one screen, not two clicks.
      setSelectedOwnerId((current) => {
        if (current && response.owners.some((o) => o.id === current)) {
          return current;
        }
        return response.owners[0]?.id ?? null;
      });
    } catch (err) {
      setOwners(null);
      setOwnersError(
        err instanceof Error ? err.message : "Failed to load owner profiles"
      );
    } finally {
      setOwnersLoading(false);
    }
  }, []);

  const loadModules = useCallback(async (ownerId: string) => {
    setModulesLoading(true);
    setModulesError(null);
    try {
      const response = await getOwnerModuleSettings(ownerId);
      setModules(response.modules);
    } catch (err) {
      setModules(null);
      setModulesError(
        err instanceof Error ? err.message : "Failed to load module entitlements"
      );
    } finally {
      setModulesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOwners();
  }, [loadOwners]);

  useEffect(() => {
    if (selectedOwnerId) {
      setModules(null);
      void loadModules(selectedOwnerId);
    }
  }, [selectedOwnerId, loadModules]);

  const selectedOwner =
    owners?.find((o) => o.id === selectedOwnerId) ?? null;

  /**
   * Apply one grant/revoke pessimistically: PATCH, then refetch the whole
   * table so the rendered state is always the server's resolved state. On
   * failure the table is refetched too, so a half-applied assumption can
   * never linger on screen.
   */
  const applyChange = useCallback(
    async (ownerId: string, module: ModuleSettingState, enabled: boolean) => {
      setSavingKey(module.moduleKey);
      setActionError(null);
      try {
        await setOwnerModuleEnabled(ownerId, module.moduleKey, enabled);
      } catch (err) {
        setActionError(
          err instanceof Error
            ? `${enabled ? "Granting" : "Revoking"} “${moduleLabel(module.moduleKey)}” failed: ${err.message}`
            : "The change could not be applied."
        );
      } finally {
        setSavingKey(null);
        await loadModules(ownerId);
      }
    },
    [loadModules]
  );

  const handleToggle = (module: ModuleSettingState, next: boolean) => {
    if (!selectedOwnerId) return;
    if (!next) {
      // Destructive: confirm with owner + module named before the PATCH.
      setPendingRevoke(module);
      return;
    }
    void applyChange(selectedOwnerId, module, true);
  };

  const confirmRevoke = () => {
    if (!selectedOwnerId || !pendingRevoke) return;
    const module = pendingRevoke;
    setPendingRevoke(null);
    void applyChange(selectedOwnerId, module, false);
  };

  // ---- owners: loading / error / empty are always explanatory ----

  if (ownersLoading && owners === null) {
    return <LoadingState message="Loading owner organisations..." />;
  }

  if (ownersError) {
    return (
      <div className="mx-auto max-w-3xl">
        <ErrorPanel
          title="The platform API could not be reached."
          detail={`Owner organisations could not be loaded — nothing is wrong with the entitlements themselves. (${ownersError})`}
          onRetry={() => void loadOwners()}
        />
      </div>
    );
  }

  if (!owners || owners.length === 0) {
    return (
      <div className="mx-auto max-w-3xl">
        <div className="flex flex-col items-center gap-3 rounded-xl border border-gray-200 bg-white px-6 py-12 text-center">
          <Users size={28} className="text-gray-400" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold text-gray-800">
              No owner organisations exist on this deployment yet.
            </p>
            <p className="mt-1 text-sm text-gray-500">
              An owner profile is created when a tenant organisation first
              uses the app; the platform API never creates one implicitly.
              There is nothing to administer until then — this is not an
              error.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-sm"
            onClick={() => void loadOwners()}
          >
            <RefreshCw size={14} aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </div>
    );
  }

  const toggleable = modules?.filter((m) => !m.essential) ?? [];
  const essential = modules?.filter((m) => m.essential) ?? [];

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8">
      {/* Owner list — one entry today; the surface is owner-id-scoped so
          real multi-tenancy only adds rows here. */}
      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Owner organisations
          </h2>
          <p className="text-sm text-gray-500">
            Select an organisation to administer its module entitlements.
            Grants and revocations are audited with you as the actor.
          </p>
        </div>
        <ul className="divide-y divide-gray-200 rounded-xl border border-gray-200 bg-white">
          {owners.map((owner) => (
            <li key={owner.id}>
              <button
                type="button"
                onClick={() => setSelectedOwnerId(owner.id)}
                aria-pressed={owner.id === selectedOwnerId}
                className={`flex w-full items-center justify-between gap-4 px-4 py-3 text-left transition-colors ${
                  owner.id === selectedOwnerId
                    ? "bg-priori-purple/5"
                    : "hover:bg-gray-50"
                }`}
              >
                <span className="text-sm font-medium text-gray-800">
                  {owner.fullName}
                </span>
                {owner.id === selectedOwnerId && (
                  <span className="text-xs font-semibold text-priori-purple">
                    Selected
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </section>

      {/* Entitlement table for the selected owner. */}
      {selectedOwner && (
        <section className="flex flex-col gap-3">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              Module entitlements — {selectedOwner.fullName}
            </h2>
            <p className="text-sm text-gray-500">
              The state shown is the server&apos;s resolved state (a module
              with no explicit override is enabled by default). Disabling a
              module blocks its pages and APIs for every user in the
              organisation.
            </p>
          </div>

          {actionError && (
            <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {actionError}
            </p>
          )}

          {modulesLoading && modules === null && (
            <LoadingState message="Loading module entitlements..." />
          )}

          {modulesError && (
            <ErrorPanel
              title="Module entitlements could not be loaded."
              detail={`The entitlement table for ${selectedOwner.fullName} did not load — no change has been made. (${modulesError})`}
              onRetry={() => void loadModules(selectedOwner.id)}
            />
          )}

          {modules !== null && !modulesError && (
            <>
              <ul className="divide-y divide-gray-200 rounded-xl border border-gray-200 bg-white">
                {toggleable.map((module) => (
                  <li
                    key={module.moduleKey}
                    className="flex items-center justify-between gap-4 px-4 py-3"
                  >
                    <div className="min-w-0">
                      <span className="text-sm font-medium text-gray-800">
                        {moduleLabel(module.moduleKey)}
                      </span>
                      <p className="text-xs text-gray-400">
                        {module.enabled ? "Granted" : "Revoked"}
                      </p>
                    </div>
                    <Toggle
                      checked={module.enabled}
                      disabled={savingKey !== null || modulesLoading}
                      aria-label={`${module.enabled ? "Revoke" : "Grant"} ${moduleLabel(module.moduleKey)} for ${selectedOwner.fullName}`}
                      onChange={(next) => handleToggle(module, next)}
                    />
                  </li>
                ))}
              </ul>

              <div className="mt-2">
                <h3 className="text-sm font-semibold text-gray-900">
                  Essential modules
                </h3>
                <p className="text-xs text-gray-500">
                  Core infrastructure — always enabled and never revocable.
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
                      title={ESSENTIAL_HINT}
                    >
                      <Lock size={14} aria-hidden="true" />
                      Always on
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {/* Revoke confirmation: names the owner AND the module. */}
      <Dialog
        isOpen={pendingRevoke !== null}
        onClose={() => setPendingRevoke(null)}
        title="Revoke module?"
        variant="danger"
        confirmLabel="Revoke module"
        cancelLabel="Keep enabled"
        onConfirm={confirmRevoke}
        description={
          pendingRevoke
            ? `Revoke “${moduleLabel(pendingRevoke.moduleKey)}” for ${
                selectedOwner?.fullName ?? "this organisation"
              }? Every user in the organisation immediately loses access to its pages and APIs. The change is audited and can be granted again at any time.`
            : undefined
        }
      />
    </div>
  );
}
