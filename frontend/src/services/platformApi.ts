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
export type OwnerStatus = Schema<"OwnerStatus">;
export type PlatformAuditEvent = Schema<"PlatformAuditEvent">;
export type PlatformAuditPage = Schema<"PaginatedResponse_PlatformAuditEvent_">;

export interface ListPlatformOwnersParams {
  page?: number;
  perPage?: number;
  search?: string;
  withTotal?: boolean;
}

/**
 * Paginated, name-searchable owner listing with per-owner summary
 * (lifecycle status + disabled-module count). Backward-compatible: with no
 * parameters the (large) default page returns every owner.
 */
export function listPlatformOwners(
  params: ListPlatformOwnersParams = {}
): Promise<PlatformOwnersResponse> {
  return apiGet<PlatformOwnersResponse>("platform/owners", {
    page: params.page,
    per_page: params.perPage,
    search: params.search || undefined,
    withTotal: params.withTotal,
  });
}

/**
 * Suspend or reactivate one owner (ADR-0014 Phase A). Audited server-side
 * (owner_suspended / owner_reactivated) with actor and before/after state;
 * never touches user roles. Callers must refetch pessimistically.
 */
export function setOwnerStatus(
  ownerId: string,
  status: OwnerStatus
): Promise<PlatformOwnerSummary> {
  return apiPatch<PlatformOwnerSummary>(`platform/owners/${ownerId}/status`, {
    status,
  });
}

export interface PlatformAuditParams {
  page?: number;
  perPage?: number;
  ownerId?: string;
  action?: string;
  actorId?: string;
  dateFrom?: string;
  dateTo?: string;
  withTotal?: boolean;
}

/**
 * Paginated, filterable operator audit trail: the audit_events rows written
 * by the owner service (entitlement grants/revocations + lifecycle status
 * changes), newest first. Read-only evidence.
 */
export function getPlatformAudit(
  params: PlatformAuditParams = {}
): Promise<PlatformAuditPage> {
  return apiGet<PlatformAuditPage>("platform/audit", {
    page: params.page,
    per_page: params.perPage,
    ownerId: params.ownerId,
    action: params.action,
    actorId: params.actorId,
    dateFrom: params.dateFrom,
    dateTo: params.dateTo,
    withTotal: params.withTotal,
  });
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
