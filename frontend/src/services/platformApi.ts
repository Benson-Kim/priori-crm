/**
 * Platform-operator API client (ADR-0011, issue #62 Phase B).
 *
 * Wraps the three /platform routes, all gated server-side on
 * require_role(PLATFORM_OPERATOR). Entitlements are commercial grants made
 * by the platform operator, not tenant preferences: this client is the only
 * write path the frontend has (the tenant-facing PATCH was removed in !56).
 *
 * Response types derive from the generated OpenAPI contract
 * (`@/lib/apiTypes`) — never hand-mirrored.
 */

import { apiGet, apiPatch } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";

export type PlatformOwnerSummary = Schema<"PlatformOwnerSummary">;
export type PlatformOwnersResponse = Schema<"PlatformOwnersResponse">;
export type ModuleSettingState = Schema<"ModuleSettingState">;
export type ModuleSettingsResponse = Schema<"ModuleSettingsResponse">;

/** Identity-only owner listing (id + display name) for the console. */
export function listPlatformOwners(): Promise<PlatformOwnersResponse> {
  return apiGet<PlatformOwnersResponse>("platform/owners");
}

/**
 * Every module with its RESOLVED entitlement state for one owner (a missing
 * override row means enabled) plus the essential flag. 404s on unknown ids —
 * the platform surface never creates owner profiles implicitly.
 */
export function getOwnerModuleSettings(
  ownerId: string
): Promise<ModuleSettingsResponse> {
  return apiGet<ModuleSettingsResponse>(`platform/owners/${ownerId}/modules`);
}

/**
 * Grant or revoke one toggleable module for one owner. The backend audits
 * every change and rejects essential modules with a 422. Callers must treat
 * this pessimistically: refetch the entitlement table after the PATCH
 * resolves instead of trusting optimistic local state.
 */
export function setOwnerModuleEnabled(
  ownerId: string,
  moduleKey: string,
  enabled: boolean
): Promise<ModuleSettingState> {
  return apiPatch<ModuleSettingState>(
    `platform/owners/${ownerId}/modules/${moduleKey}`,
    { enabled }
  );
}
