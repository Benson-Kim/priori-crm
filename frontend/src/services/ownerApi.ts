/**
 * Owner / document-header API client (W3.6).
 *
 * The single source of truth for the organisation identity printed on
 * documents, replacing the hardcoded COMPANY_INFO constant.
 *
 * The response type derives from the generated OpenAPI contract
 * (`@/lib/apiTypes`); the update body stays hand-written (#41).
 */
import { apiDelete, apiDownload, apiGet, apiPut, apiUploadPut } from "@/lib/api";
import type { Schema } from "@/lib/apiTypes";

// OwnerProfileResponse is serialized by alias, so the generated type is the
// camelCase shape (fullName / locationWatermark / taxPin / hasLogo / updatedAt).
export type OwnerProfile = Schema<"OwnerProfileResponse">;

export interface OwnerProfileUpdate {
  fullName: string;
  locationWatermark?: string | null;
  address?: string | null;
  email?: string | null;
  phone?: string | null;
  taxPin?: string | null;
  website?: string | null;
}

export function getOwnerProfile(): Promise<OwnerProfile> {
  return apiGet<OwnerProfile>("owner");
}

export function updateOwnerProfile(
  data: OwnerProfileUpdate
): Promise<OwnerProfile> {
  return apiPut<OwnerProfile>("owner", data);
}

export function uploadOwnerLogo(file: File): Promise<OwnerProfile> {
  const form = new FormData();
  form.append("file", file);
  return apiUploadPut<OwnerProfile>("owner/logo", form);
}

export function removeOwnerLogo(): Promise<OwnerProfile> {
  return apiDelete<OwnerProfile>("owner/logo");
}

/**
 * Fetch the served logo binary through the shared client so the request
 * carries the bearer token and the 401 -> refresh -> retry flow (ISSUE-028).
 *
 * The logo endpoint requires authentication, so a bare <img src> 401s and
 * never renders. Callers turn the returned Blob into an object URL (and must
 * revoke it when done) — mirroring the expense-document download pattern.
 */
export function fetchOwnerLogo(): Promise<Blob> {
  return apiDownload("owner/logo");
}
