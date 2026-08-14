/**
 * Settings › Documents — organisation-wide document defaults.
 *
 * Jurisdiction (drives the compliance-reference label), the default Terms &
 * Conditions and the default Send-email message. Stored once at org scope
 * and applied to purchase orders at CREATE TIME ONLY — editing a default
 * never alters an existing document.
 *
 * Sends only the document-default fields (plus the always-required business
 * name); the backend PUT applies exclude_unset semantics, so business
 * details edited on the Organisation tab are never wiped from here.
 */

import { CheckCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { Select } from "@/components/ui/Select";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { COUNTRY_OPTIONS } from "@/lib/constants";

// Mirror the backend caps (api schema MAX_DEFAULT_TERMS_LENGTH).
const MAX_DEFAULT_TERMS_LENGTH = 2000;

interface DocumentDefaults {
  jurisdiction: string;
  defaultTermsAndConditions: string;
  defaultSendMessage: string;
}

export default function DocumentSettingsPage() {
  const { profile, loading, save } = useOwnerProfile();

  const [form, setForm] = useState<DocumentDefaults | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (profile && form === null) {
      setForm({
        jurisdiction: profile.jurisdiction ?? "",
        defaultTermsAndConditions: profile.defaultTermsAndConditions ?? "",
        defaultSendMessage: profile.defaultSendMessage ?? "",
      });
    }
  }, [profile, form]);

  if (loading && !profile) {
    return <LoadingState message="Loading document settings..." />;
  }

  const update = (key: keyof DocumentDefaults, value: string) => {
    setSaved(false);
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const handleSave = async () => {
    if (!form || !profile) return;
    setError(null);
    setSaving(true);
    try {
      // fullName is required by the update schema; everything else in the
      // profile is left untouched (exclude_unset on the backend).
      await save({
        fullName: profile.fullName,
        jurisdiction: form.jurisdiction || null,
        defaultTermsAndConditions: form.defaultTermsAndConditions || null,
        defaultSendMessage: form.defaultSendMessage || null,
      });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-3xl flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Document defaults
          </h2>
          <p className="text-sm text-gray-500">
            Applied to new documents at create time only — editing a default
            never changes documents that already exist.
          </p>
        </div>

        <div className="flex flex-col gap-5 rounded-xl border border-gray-200 bg-white p-6">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-semibold text-gray-800">
              Jurisdiction
            </span>
            <Select
              value={form?.jurisdiction ?? ""}
              onChange={(e) => update("jurisdiction", e.target.value)}
              options={COUNTRY_OPTIONS}
            />
            <span className="text-xs text-gray-400">
              Drives the compliance-reference label printed on documents.
            </span>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-semibold text-gray-800">
              Default Terms &amp; Conditions
            </span>
            <textarea
              value={form?.defaultTermsAndConditions ?? ""}
              onChange={(e) =>
                update("defaultTermsAndConditions", e.target.value)
              }
              rows={5}
              maxLength={MAX_DEFAULT_TERMS_LENGTH}
              placeholder="Prefilled on new purchase orders"
              className="border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-priori-purple resize-none"
            />
            <span className="text-xs text-gray-400 self-end">
              {(form?.defaultTermsAndConditions ?? "").length}/
              {MAX_DEFAULT_TERMS_LENGTH}
            </span>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-semibold text-gray-800">
              Default Send Message
            </span>
            <textarea
              value={form?.defaultSendMessage ?? ""}
              onChange={(e) => update("defaultSendMessage", e.target.value)}
              rows={4}
              placeholder="Default message used when sending a document"
              className="border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-priori-purple resize-none"
            />
          </label>
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
