/**
 * OwnerProfileModal — edit the document-owner profile.
 *
 * Editing here updates *live* document headers but, by
 * the locked product decision, never re-brands already-issued documents.
 */
import { useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { ACCEPTED_IMAGE_TYPES } from "@/lib/constants";
import { COUNTRIES } from "@/lib/countries";
import { cn } from "@/lib/utils";
import { type OwnerProfile, type OwnerProfileUpdate } from "@/services/ownerApi";
import { CheckCircle, SquarePen, Trash, UploadCloud, X } from "lucide-react";
import { Input } from "../ui/Input";
import { Select } from "../ui/Select";

// Mirror the backend PO Terms & Conditions cap (api schema MAX_DEFAULT_TERMS_LENGTH).
const MAX_DEFAULT_TERMS_LENGTH = 2000;

interface OwnerProfileModalProps {
  profile: OwnerProfile | null;
  onClose: () => void;
  onSave: (data: OwnerProfileUpdate) => Promise<void>;
}

const FIELDS: { key: keyof OwnerProfileUpdate; label: string; type?: string; placeholder?: string; prefix?: string }[] = [
  { key: "fullName", label: "Full Name", placeholder: "Enter full name" },
  { key: "locationWatermark", label: "Location", placeholder: "Watermark" },
  { key: "address", label: "Address", placeholder: "PO BOX 000, 10000" },
  { key: "email", label: "Email", type: "email", placeholder: "email@example.com" },
  { key: "phone", label: "Phone", placeholder: "700 000 000", prefix: "+254" },
  { key: "taxPin", label: "Tax ID/PIN Number", placeholder: "AOE0387233N" },
  { key: "website", label: "Website", placeholder: "https://example.com" },
];

export function OwnerProfileModal({
  profile,
  onClose,
  onSave,
}: Readonly<OwnerProfileModalProps>) {
  const [form, setForm] = useState<OwnerProfileUpdate>({
    fullName: profile?.fullName ?? "",
    locationWatermark: profile?.locationWatermark ?? "",
    address: profile?.address ?? "",
    email: profile?.email ?? "",
    phone: profile?.phone ?? "",
    taxPin: profile?.taxPin ?? "",
    website: profile?.website ?? "",
    // Preserve org-scoped defaults so they aren't wiped on save.
    defaultTermsAndConditions: profile?.defaultTermsAndConditions ?? "",
    defaultSendMessage: profile?.defaultSendMessage ?? "",
    jurisdiction: profile?.jurisdiction ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const { logoUrl, uploadLogo, removeLogo } = useOwnerProfile();

  const handleLogoPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];

    try {
      if (file) {
        await uploadLogo(file);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload logo.");
    } finally {
      e.target.value = "";
    }
  };

  const update = (key: keyof OwnerProfileUpdate, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const handleSave = async () => {
    setError(null);
    if (!form.fullName?.trim()) {
      setError("Full name is required.");
      return;
    }
    setSaving(true);
    try {
      await onSave(form);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save profile.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative w-full max-w-2xl mx-4 rounded-xl bg-gray-100 p-6 shadow-xl overflow-hidden max-h-[90vh] overflow-y-auto",
          "animate-in fade-in zoom-in-95 duration-200"
        )}
      >
        <div className="flex items-center justify-between border-b border-gray-300 mb-8">
          <div className="flex items-center gap-2 text-gray-800">
            <SquarePen size={24} strokeWidth={2} />
            <h3 className="text-2xl font-bold py-3">{profile?.fullName ?? "Owner Profile"}</h3>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            aria-label="Close dialog"
          >
            <X size={32} strokeWidth={2} />
          </button>
        </div>

        <div className="flex flex-col gap-6 p-6 bg-white border border-gray-200 rounded-xl">
          <div className="flex items-center justify-between gap-6">
            {logoUrl ? (
              <img src={logoUrl} alt={`${profile?.fullName ?? "Owner"} logo`}
                className="max-h-12 w-auto"
              />
            ) : (
                <p className="h-12 flex items-center text-gray-400 text-sm">
                No logo
                </p>
            )}
            <div className="flex items-center gap-6">
              <Button variant="outline-secondary"
                onClick={() => fileInputRef.current?.click()}
                disabled={saving}
                className="flex items-center justify-center gap-2 text-[20px] border-gray-300 text-gray-600">
                <UploadCloud size={20} /> Upload
              </Button>
              <Button variant="outline-secondary"
                onClick={async () => { await removeLogo() }}
                disabled={saving}
                className="flex items-center justify-center gap-2 text-[20px] border-gray-300 text-gray-600">
                <Trash size={20} /> Remove
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_IMAGE_TYPES}
                className="hidden"
                onChange={handleLogoPick}
              />
            </div>
          </div>

          {FIELDS.map(({ key, label, type, placeholder, prefix }) => (
            <label key={key} className="flex flex-col gap-2">
              <span className="text-base font-bold text-gray-800">{label}</span>
              <Input
                type={type ?? "text"}
                value={(form[key] as string) ?? ""}
                onChange={(e) => update(key, e.target.value)}
                placeholder={placeholder}
                prefix={prefix ? <span className="text-gray-500 text-base font-medium">{prefix}</span> : undefined}
                wrapperClassName="bg-white"
              />
            </label>
          ))}

          {/* Org-scoped Purchase Order defaults. Stored once at org
              scope; applied to new POs at create time only. */}
          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium text-gray-600">
              Jurisdiction
            </span>
            <Select
              value={form.jurisdiction ?? ""}
              onChange={(e) => update("jurisdiction", e.target.value)}
              options={COUNTRIES}
              placeholder="Select country"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium text-gray-600">
              Default Terms &amp; Conditions
            </span>
            <textarea
              value={form.defaultTermsAndConditions ?? ""}
              onChange={(e) =>
                update("defaultTermsAndConditions", e.target.value)
              }
              rows={3}
              maxLength={MAX_DEFAULT_TERMS_LENGTH}
              placeholder="Prefilled on new purchase orders"
              className="border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-priori-purple resize-none"
            />
            <span className="text-xs text-gray-400 self-end">
              {(form.defaultTermsAndConditions ?? "").length}/
              {MAX_DEFAULT_TERMS_LENGTH}
            </span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium text-gray-600">
              Default Send Message
            </span>
            <textarea
              value={form.defaultSendMessage ?? ""}
              onChange={(e) => update("defaultSendMessage", e.target.value)}
              rows={3}
              placeholder="Default message used when sending a purchase order"
              className="border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-priori-purple resize-none"
            />
          </label>
        </div>

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        <div className="w-full flex items-center justify-between gap-8 mt-8">
          <Button type="button"
            variant="outline-secondary"
            onClick={onClose}
            disabled={saving}
            className="w-full flex items-center justify-center gap-2 text-[20px] border-gray-300 text-gray-600">
            <X size={20} /> Cancel
          </Button>
          <Button type="button"
            variant="primary"
            onClick={handleSave}
            loading={saving}
            className="w-full flex items-center justify-center gap-2 text-[20px]">
            <CheckCircle size={24} className="font-bold" /> Save
          </Button>
        </div>
      </div>
    </div>
  );
}
