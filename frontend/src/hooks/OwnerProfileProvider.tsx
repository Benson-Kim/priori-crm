/**
 * OwnerProfileProvider — single source of truth for the document-owner
 * profile and its authenticated logo blob
 *
 * Previously useOwnerProfile fetched the profile (and logo) on every
 * DocumentOwnerHeader mount, so each document surface re-hit the API. This
 * provider performs the fetch once, owns the logo object-URL lifecycle
 * (revoke-on-replace + revoke-on-unmount), and refetches the blob
 * only when the logo identity changes (keyed on hasLogo + updatedAt). All
 * consumers share the same state via useOwnerProfile().
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  OwnerProfileContext,
  type OwnerProfileContextValue,
} from "@/hooks/owner-profile-context";
import {
  fetchOwnerLogo,
  getOwnerProfile,
  removeOwnerLogo,
  updateOwnerProfile,
  uploadOwnerLogo,
  type OwnerProfile,
  type OwnerProfileUpdate,
} from "@/services/ownerApi";

export function OwnerProfileProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<OwnerProfile | null>(null);
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Track the live object URL so it can be revoked before being replaced and
  // on unmount, avoiding a blob-URL memory leak.
  const logoUrlRef = useRef<string | null>(null);
  const setLogoObjectUrl = useCallback((url: string | null) => {
    if (logoUrlRef.current) {
      URL.revokeObjectURL(logoUrlRef.current);
    }
    logoUrlRef.current = url;
    setLogoUrl(url);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProfile(await getOwnerProfile());
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load owner profile"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await refresh();
    })();
  }, [refresh]);

  // Load (or clear) the authenticated logo blob whenever the logo identity
  // changes. Keyed on hasLogo + updatedAt so an upload/remove refetches.
  const hasLogo = profile?.hasLogo ?? false;
  const logoVersion = profile?.updatedAt ?? null;
  useEffect(() => {
    if (!hasLogo) {
      setLogoObjectUrl(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const blob = await fetchOwnerLogo(logoVersion || undefined);
        if (!cancelled) {
          setLogoObjectUrl(URL.createObjectURL(blob));
        }
      } catch {
        if (!cancelled) {
          setLogoObjectUrl(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hasLogo, logoVersion, setLogoObjectUrl]);

  // Revoke the last object URL on unmount.
  useEffect(() => {
    return () => {
      if (logoUrlRef.current) {
        URL.revokeObjectURL(logoUrlRef.current);
        logoUrlRef.current = null;
      }
    };
  }, []);

  const save = useCallback(async (data: OwnerProfileUpdate) => {
    setProfile(await updateOwnerProfile(data));
  }, []);

  const uploadLogo = useCallback(async (file: File) => {
    setProfile(await uploadOwnerLogo(file));
  }, []);

  const removeLogo = useCallback(async () => {
    setProfile(await removeOwnerLogo());
  }, []);

  const value = useMemo<OwnerProfileContextValue>(
    () => ({
      profile,
      logoUrl,
      loading,
      error,
      refresh,
      save,
      uploadLogo,
      removeLogo,
    }),
    [profile, logoUrl, loading, error, refresh, save, uploadLogo, removeLogo]
  );

  return (
    <OwnerProfileContext.Provider value={value}>
      {children}
    </OwnerProfileContext.Provider>
  );
}
