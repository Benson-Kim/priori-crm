/**
 * Settings › Organisation — business details, branding and logo.
 *
 * The owner profile finally has a settings home outside the document editor
 * (issue #58): the same live profile the document headers render, edited
 * here at organisation scope. Saving updates live headers only — issued
 * documents keep their immutable snapshots (locked product decision).
 *
 * Sends only the business-detail fields; the backend PUT applies
 * exclude_unset semantics, so document defaults edited on the Documents tab
 * are never wiped from here.
 */

import { CheckCircle, Trash, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { ACCEPTED_IMAGE_TYPES } from "@/lib/constants";
import type { OwnerProfileUpdate } from "@/services/ownerApi";

type BusinessDetails = Pick<
  OwnerProfileUpdate,
  | "fullName"
  | "locationWatermark"
  | "address"
  | "email"
  | "phone"
  | "taxPin"
  | "website"
>;

const FIELDS: {
  key: keyof BusinessDetails;
  label: string;
  type?: string;
  placeholder?: string;
  prefix?: string;
}[] = [
  { key: "fullName", label: "Business name", placeholder: "Enter full name" },
  { key: "locationWatermark", label: "Location", placeholder: "Watermark" },
  { key: "address", label: "Address", placeholder: "PO BOX 000, 10000" },
  { key: "email", label: "Email", type: "email", placeholder: "email@example.com" },
  { key: "phone", label: "Phone", placeholder: "700 000 000", prefix: "+254" },
  { key: "taxPin", label: "Tax ID/PIN Number", placeholder: "AOE0387233N" },
  { key: "website", label: "Website", placeholder: "https://example.com" },
];

export default function OrganisationSettingsPage() {
  const { profile, logoUrl, loading, save, uploadLogo, removeLogo } =
    useOwnerProfile();

  const [form, setForm] = useState<BusinessDetails | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Seed the form once the profile bootstrap lands (and after saves).
  useEffect(() => {
    if (profile && form === null) {
      setForm({
        fullName: profile.fullName ?? "",
        locationWatermark: profile.locationWatermark ?? "",
        address: profile.address ?? "",
        email: profile.email ?? "",
        phone: profile.phone ?? "",
        taxPin: profile.taxPin ?? "",
        website: profile.website ?? "",
      });
    }
  }, [profile, form]);

  if (loading && !profile) {
    return <LoadingState message="Loading organisation settings..." />;
  }

  const update = (key: keyof BusinessDetails, value: string) => {
    setSaved(false);
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const handleLogoPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    setError(null);
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

  const handleSave = async () => {
    if (!form) return;
    setError(null);
    if (!form.fullName?.trim()) {
      setError("Business name is required.");
      return;
    }
    setSaving(true);
    try {
      await save(form);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-3xl flex flex-col gap-8">
      {/* Branding & logo */}
      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Branding &amp; logo
          </h2>
          <p className="text-sm text-gray-500">
            Shown on every live document header. Already-issued documents are
            never re-branded.
          </p>
        </div>
        <div className="flex items-center justify-between gap-6 rounded-xl border border-gray-200 bg-white px-4 py-4">
          {logoUrl ? (
            <img
              src={logoUrl}
              alt={`${profile?.fullName ?? "Organisation"} logo`}
              className="max-h-12 w-auto"
            />
          ) : (
            <p className="h-12 flex items-center text-gray-400 text-sm">
              No logo
            </p>
          )}
          <div className="flex items-center gap-3">
            <Button
              variant="outline-secondary"
              onClick={() => fileInputRef.current?.click()}
              disabled={saving}
              className="flex items-center justify-center gap-2 border-gray-300 text-gray-600"
            >
              <UploadCloud size={18} /> Upload
            </Button>
            <Button
              variant="outline-secondary"
              onClick={async () => {
                setError(null);
                try {
                  await removeLogo();
                } catch (err) {
                  setError(
                    err instanceof Error
                      ? err.message
                      : "Failed to remove logo."
                  );
                }
              }}
              disabled={saving}
              className="flex items-center justify-center gap-2 border-gray-300 text-gray-600"
            >
              <Trash size={18} /> Remove
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
      </section>

      {/* Business details */}
      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Business details
          </h2>
          <p className="text-sm text-gray-500">
            The organisation identity printed on quotes, invoices and
            purchase orders.
          </p>
        </div>
        <div className="flex flex-col gap-5 rounded-xl border border-gray-200 bg-white p-6">
          {FIELDS.map(({ key, label, type, placeholder, prefix }) => (
            <label key={key} className="flex flex-col gap-1.5">
              <span className="text-sm font-semibold text-gray-800">
                {label}
              </span>
              <Input
                type={type ?? "text"}
                value={(form?.[key] as string) ?? ""}
                onChange={(e) => update(key, e.target.value)}
                placeholder={placeholder}
                prefix={
                  prefix ? (
                    <span className="text-gray-500 text-base font-medium">
                      {prefix}
                    </span>
                  ) : undefined
                }
                wrapperClassName="bg-white"
              />
            </label>
          ))}
        </div>
      </section>

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="flex items-center gap-4">
        <Button
          type="button"
          variant="primary"
          onClick={handleSave}
          loading={saving}
          className="flex items-center justify-center gap-2"
        >
          <CheckCircle size={18} /> Save changes
        </Button>
        {saved && !error && (
          <span className="text-sm text-green-700">Saved.</span>
        )}
      </div>
    </div>
  );
}
