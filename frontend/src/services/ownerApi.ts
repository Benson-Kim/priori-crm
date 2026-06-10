/**
 * Owner / document-header API client (W3.6).
 *
 * The single source of truth for the organisation identity printed on
 * documents, replacing the hardcoded COMPANY_INFO constant.
 */
import { apiDelete, apiDownload, apiGet, apiPut, apiUploadPut } from "@/lib/api";

export interface OwnerProfile {
  fullName: string;
  locationWatermark: string | null;
  address: string | null;
  email: string | null;
  phone: string | null;
  taxPin: string | null;
  website: string | null;
  hasLogo: boolean;
  updatedAt: string | null;
}

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
