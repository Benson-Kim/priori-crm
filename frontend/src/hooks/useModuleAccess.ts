/**
 * Per-owner module entitlement hooks.
 *
 * Reads the `enabledModules` map from the owner-profile bootstrap
 * (OwnerProfileProvider). Fails OPEN by design: while the profile is still
 * loading — or if a module key is missing from the map — the module is
 * treated as ENABLED, mirroring the backend's "missing row = enabled"
 * default. The backend gate (403) remains the source of truth; these hooks
 * only drive nav visibility and route redirects.
 */

import { useOwnerProfile } from "@/hooks/owner-profile-context";

export type EnabledModulesMap = Record<string, boolean>;

/**
 * The effective module entitlement map from the bootstrap response, or
 * null while it is still loading / unavailable (treated as all-enabled).
 */
export function useEnabledModules(): {
  enabledModules: EnabledModulesMap | null;
  loading: boolean;
} {
  const { profile, loading } = useOwnerProfile();
  return {
    enabledModules: profile?.enabledModules ?? null,
    loading,
  };
}

/**
 * Whether one module is enabled. Missing map (loading/error) or missing
 * key = enabled, so nothing flickers off during bootstrap.
 */
export function useModuleEnabled(moduleKey: string): boolean {
  const { enabledModules } = useEnabledModules();
  if (!enabledModules) {
    return true;
  }
  return enabledModules[moduleKey] !== false;
}
