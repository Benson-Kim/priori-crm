/**
 * Owner-profile context + hook (ISSUE-034).
 *
 * Kept separate from the OwnerProfileProvider component so the provider file
 * exports only a component (eslint react-refresh/only-export-components). The
 * provider (hooks/OwnerProfileProvider.tsx) owns the single fetch of the
 * profile + authenticated logo blob and shares it across every
 * DocumentOwnerHeader, so mounting the header on multiple document surfaces no
 * longer triggers a fetch per mount.
 */

import { createContext, useContext } from "react";

import type { OwnerProfile, OwnerProfileUpdate } from "@/services/ownerApi";

export interface OwnerProfileContextValue {
  profile: OwnerProfile | null;
  /** Object URL of the authenticated logo blob, or null when there is none. */
  logoUrl: string | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  save: (data: OwnerProfileUpdate) => Promise<void>;
  uploadLogo: (file: File) => Promise<void>;
  removeLogo: () => Promise<void>;
}

export const OwnerProfileContext = createContext<
  OwnerProfileContextValue | undefined
>(undefined);

export function useOwnerProfile(): OwnerProfileContextValue {
  const ctx = useContext(OwnerProfileContext);
  if (ctx === undefined) {
    throw new Error(
      "useOwnerProfile must be used within an OwnerProfileProvider"
    );
  }
  return ctx;
}
