/**
 * DocumentOwnerHeader — the single owner identity block rendered on every
 * document (V-SOLID-4). Replaces the duplicated COMPANY_INFO blocks in
 * DocumentEditor / DocumentViewer / ExpenseViewer and the vendor statement.
 *
 * In editable mode it exposes the logo upload/remove controls and an edit
 * modal behind the previously-dead "Update" buttons.
 */
import { SquarePen, Trash2 } from "lucide-react";
import { useRef, useState } from "react";

import { useOwnerProfile } from "@/hooks/useOwnerProfile";
import { ownerLogoUrl } from "@/services/ownerApi";
import { OwnerProfileModal } from "./OwnerProfileModal";

interface DocumentOwnerHeaderProps {
  /** When true, render the logo + profile edit controls (editor only). */
  editable?: boolean;
}

export function DocumentOwnerHeader({
  editable = false,
}: Readonly<DocumentOwnerHeaderProps>) {
  const { profile, save, uploadLogo, removeLogo } = useOwnerProfile();
  const [modalOpen, setModalOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const cacheBust = profile?.updatedAt ?? undefined;

  const handleLogoPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      await uploadLogo(file);
    }
    e.target.value = "";
  };

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Logo */}
        <div className="flex flex-col items-start gap-3">
          {profile?.hasLogo ? (
            <img
              src={ownerLogoUrl(cacheBust)}
              alt={`${profile.fullName} logo`}
              className="max-h-12 w-auto"
            />
          ) : (
            <div className="h-12 flex items-center text-gray-400 text-sm">
              No logo
            </div>
          )}
          {editable && (
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="text-priori-purple font-bold text-sm flex items-center gap-1 hover:underline"
              >
                Update <SquarePen size={18} />
              </button>
              {profile?.hasLogo && (
                <button
                  type="button"
                  onClick={() => removeLogo()}
                  className="text-red-500 font-bold text-sm flex items-center gap-1 hover:underline"
                >
                  Remove <Trash2 size={18} />
                </button>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                className="hidden"
                onChange={handleLogoPick}
              />
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
          {editable && (
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="text-priori-purple font-bold text-sm flex items-center gap-1 hover:underline w-fit mt-1"
            >
              Update <SquarePen size={18} />
            </button>
          )}
        </div>
      </div>

      {modalOpen && (
        <OwnerProfileModal
          profile={profile}
          onClose={() => setModalOpen(false)}
          onSave={save}
        />
      )}
    </>
  );
}
