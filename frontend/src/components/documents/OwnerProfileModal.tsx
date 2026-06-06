/**
 * OwnerProfileModal — edit the document-owner profile (W3.6 / S-1).
 *
 * Opened from the "Update" controls in the document header. Saves through
 * the shared ownerApi; editing here updates *live* document headers but, by
 * the locked product decision, never re-brands already-issued documents.
 */
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import type { OwnerProfile, OwnerProfileUpdate } from "@/services/ownerApi";

interface OwnerProfileModalProps {
  profile: OwnerProfile | null;
  onClose: () => void;
  onSave: (data: OwnerProfileUpdate) => Promise<void>;
}

const FIELDS: { key: keyof OwnerProfileUpdate; label: string; type?: string }[] = [
  { key: "fullName", label: "Full Name" },
  { key: "locationWatermark", label: "Location / Watermark" },
  { key: "address", label: "Address" },
  { key: "email", label: "Email", type: "email" },
  { key: "phone", label: "Phone" },
  { key: "taxPin", label: "Tax ID / PIN" },
  { key: "website", label: "Website" },
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
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-2xl shadow-lg w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        <h2 className="text-xl font-bold text-gray-800 mb-4">
          Document Owner Details
        </h2>

        <div className="flex flex-col gap-4">
          {FIELDS.map(({ key, label, type }) => (
            <label key={key} className="flex flex-col gap-1">
              <span className="text-sm font-medium text-gray-600">{label}</span>
              <input
                type={type ?? "text"}
                value={(form[key] as string) ?? ""}
                onChange={(e) => update(key, e.target.value)}
                className="border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-priori-purple"
              />
            </label>
          ))}
        </div>

        {error && (
          <p className="mt-3 text-sm text-red-600">{error}</p>
        )}

        <div className="flex justify-end gap-3 mt-6">
          <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="button" variant="primary" onClick={handleSave} loading={saving}>
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}
