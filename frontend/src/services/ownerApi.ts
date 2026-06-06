/**
 * Owner / document-header API client (W3.6).
 *
 * The single source of truth for the organisation identity printed on
 * documents, replacing the hardcoded COMPANY_INFO constant.
 */
import { apiDelete, apiGet, apiPut, apiUploadPut, appConfig } from "@/lib/api";

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
 * Absolute URL to the served logo binary. A cache-busting token (e.g. the
 * profile's updatedAt) forces the <img> to refetch after an upload/remove.
 */
export function ownerLogoUrl(cacheBust?: string | null): string {
  const base = `${appConfig.apiUrl}owner/logo`;
  return cacheBust ? `${base}?v=${encodeURIComponent(cacheBust)}` : base;
}
