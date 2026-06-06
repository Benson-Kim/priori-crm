/**
 * useOwnerProfile — load and mutate the single document-owner profile (W3.6).
 *
 * Replaces the hardcoded COMPANY_INFO constant as the source of truth for
 * the document header. Lightweight (no external query lib): fetch on mount,
 * expose refresh + mutators that update local state from the API response.
 */
import { useCallback, useEffect, useState } from "react";

import {
  getOwnerProfile,
  removeOwnerLogo,
  updateOwnerProfile,
  uploadOwnerLogo,
  type OwnerProfile,
  type OwnerProfileUpdate,
} from "@/services/ownerApi";

export interface UseOwnerProfile {
  profile: OwnerProfile | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  save: (data: OwnerProfileUpdate) => Promise<void>;
  uploadLogo: (file: File) => Promise<void>;
  removeLogo: () => Promise<void>;
}

export function useOwnerProfile(): UseOwnerProfile {
  const [profile, setProfile] = useState<OwnerProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProfile(await getOwnerProfile());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load owner profile");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = useCallback(async (data: OwnerProfileUpdate) => {
    setProfile(await updateOwnerProfile(data));
  }, []);

  const uploadLogo = useCallback(async (file: File) => {
    setProfile(await uploadOwnerLogo(file));
  }, []);

  const removeLogo = useCallback(async () => {
    setProfile(await removeOwnerLogo());
  }, []);

  return { profile, loading, error, refresh, save, uploadLogo, removeLogo };
}
