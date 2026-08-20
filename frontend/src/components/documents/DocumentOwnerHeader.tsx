/**
 * DocumentOwnerHeader — the single owner identity block rendered on every
 * document.
 *
 * Display-only: owner details are edited exclusively in Settings
 * (/settings/organisation). On editor surfaces, privileged users get a
 * subtle "Edit in Settings" link instead of inline edit controls.
 */
import { useState } from "react";
import { Link } from "react-router-dom";

import { useCanEditOwner } from "@/hooks/auth-context";
import { useOwnerProfile } from "@/hooks/owner-profile-context";

interface DocumentOwnerHeaderProps {
  /** When true, show the "Edit in Settings" link (editor surfaces only). */
  editable?: boolean;
}

export function DocumentOwnerHeader({
  editable = false,
}: Readonly<DocumentOwnerHeaderProps>) {
  const { profile, logoUrl } = useOwnerProfile();
  // Only privileged users (MANAGER/ADMIN) may edit; the backend 403s everyone
  // else, so never show the Settings link to them
  const canEdit = useCanEditOwner();
  const showEdit = editable && canEdit;
  // Remember which URL failed instead of resetting an error flag in an
  // effect: a new logoUrl no longer matches the failed one, so the error
  // state "resets" by derivation (react.dev "you might not need an effect").
  const [erroredLogoUrl, setErroredLogoUrl] = useState<string | null>(null);

  const hasLogo = Boolean(logoUrl && erroredLogoUrl !== logoUrl);

  return (
    <div className="flex flex-col gap-6">
      {/* Logo */}
      <div className="flex flex-col items-start gap-3">
        {hasLogo ? (
          <img
            src={logoUrl as string}
            alt=""
            onError={() => setErroredLogoUrl(logoUrl)}
            className="max-h-12 w-auto"
          />
        ) : (
          <div className="h-12 flex items-center text-gray-400 text-sm">
            {profile?.hasLogo ? "" : "No logo"}
          </div>
        )}
      </div>

      {/* Owner identity */}
      <div className="flex flex-col gap-1 text-gray-800">
        <h3 className="font-bold text-base mb-1">{profile?.fullName ?? ""}</h3>
        {profile?.address && <p className="text-sm">{profile.address}</p>}
        {profile?.phone && <p className="text-sm">{profile.phone}</p>}
        {profile?.email && <p className="text-sm">{profile.email}</p>}
        {profile?.taxPin && (
          <p className="text-sm">Tax PIN: {profile.taxPin}</p>
        )}
        {profile?.website && <p className="text-sm">{profile.website}</p>}
        {showEdit && (
          <Link
            to="/settings/organisation"
            className="text-gray-400 text-xs w-fit mt-1 hover:text-priori-purple hover:underline"
          >
            Edit in Settings
          </Link>
        )}
      </div>
    </div>
  );
}
