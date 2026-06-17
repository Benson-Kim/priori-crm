/**
 * OwnerProfileModal — edit the document-owner profile.
 *
 * Opened from the "Update" controls in the document header. Saves through
 * the shared ownerApi; editing here updates *live* document headers but, by
 * the locked product decision, never re-brands already-issued documents.
 */
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { COUNTRY_OPTIONS } from "@/lib/constants";
import type { OwnerProfile, OwnerProfileUpdate } from "@/services/ownerApi";

// Mirror the backend PO Terms & Conditions cap (api schema MAX_DEFAULT_TERMS_LENGTH).
const MAX_DEFAULT_TERMS_LENGTH = 2000;

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
    // Org-scoped document-settings defaults (PO-11). The GET/PUT response
    // returns resolved values, so these prefill with the persisted value or
    // the built-in default.
    defaultTermsAndConditions: profile?.defaultTermsAndConditions ?? "",
    defaultSendMessage: profile?.defaultSendMessage ?? "",
    jurisdiction: profile?.jurisdiction ?? "",
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

          {/* Org-scoped Purchase Order defaults (PO-11). Stored once at org
              scope; applied to new POs at create time only. */}
          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium text-gray-600">
              Jurisdiction
            </span>
            <Select
              value={form.jurisdiction ?? ""}
              onChange={(e) => update("jurisdiction", e.target.value)}
              options={COUNTRY_OPTIONS}
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
