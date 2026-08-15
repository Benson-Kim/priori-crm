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
 *
 * Uses react-hook-form + zod (see validations/settingsSchema) so the Settings
 * pages validate the same way as the customer/vendor forms.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";

import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { Select } from "@/components/ui/Select";
import { useOwnerProfile } from "@/hooks/owner-profile-context";
import { COUNTRY_OPTIONS } from "@/lib/constants";
import {
  documentDefaultsSchema,
  MAX_DEFAULT_TERMS_LENGTH,
  type DocumentDefaultsFormData,
} from "@/validations/settingsSchema";

const EMPTY_FORM: DocumentDefaultsFormData = {
  jurisdiction: "",
  defaultTermsAndConditions: "",
  defaultSendMessage: "",
};

const textareaClass =
  "border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-priori-purple resize-none";

export default function DocumentSettingsPage() {
  const { profile, loading, save } = useOwnerProfile();

  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Seed from the first profile that lands and never again, so a later
  // bootstrap settle cannot overwrite what the user is typing.
  const seeded = useRef(false);

  const {
    control,
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<DocumentDefaultsFormData>({
    resolver: zodResolver(documentDefaultsSchema),
    defaultValues: EMPTY_FORM,
  });

  useEffect(() => {
    if (profile && !seeded.current) {
      seeded.current = true;
      reset({
        jurisdiction: profile.jurisdiction ?? "",
        defaultTermsAndConditions: profile.defaultTermsAndConditions ?? "",
        defaultSendMessage: profile.defaultSendMessage ?? "",
      });
    }
  }, [profile, reset]);

  // useWatch rather than watch(): it keeps the live character counter in sync
  // without opting the file out of the React Compiler.
  const terms = useWatch({ control, name: "defaultTermsAndConditions" });
  const termsLength = (terms ?? "").length;

  if (loading && !profile) {
    return <LoadingState message="Loading document settings..." />;
  }

  const onSubmit = async (data: DocumentDefaultsFormData) => {
    // fullName is read from the loaded profile, not the form, so the profile
    // has to be here before this tab can save at all.
    if (!profile) return;
    setError(null);
    try {
      // fullName is required by the update schema; everything else in the
      // profile is left untouched (exclude_unset on the backend).
      await save({
        fullName: profile.fullName,
        jurisdiction: data.jurisdiction || null,
        defaultTermsAndConditions: data.defaultTermsAndConditions || null,
        defaultSendMessage: data.defaultSendMessage || null,
      });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings.");
    }
  };

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      // React's onChange is the bubbling input event, so any edit in any
      // field below invalidates the "Saved." confirmation.
      onChange={() => setSaved(false)}
      noValidate
      className="max-w-3xl flex flex-col gap-8"
    >
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
              {...register("jurisdiction")}
              options={COUNTRY_OPTIONS}
              error={errors.jurisdiction?.message}
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
              {...register("defaultTermsAndConditions")}
              rows={5}
              maxLength={MAX_DEFAULT_TERMS_LENGTH}
              placeholder="Prefilled on new purchase orders"
              className={textareaClass}
            />
            {errors.defaultTermsAndConditions && (
              <span className="text-xs text-red-500">
                {errors.defaultTermsAndConditions.message}
              </span>
            )}
            <span className="text-xs text-gray-400 self-end">
              {termsLength}/{MAX_DEFAULT_TERMS_LENGTH}
            </span>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-semibold text-gray-800">
              Default Send Message
            </span>
            <textarea
              {...register("defaultSendMessage")}
              rows={4}
              placeholder="Default message used when sending a document"
              className={textareaClass}
            />
            {errors.defaultSendMessage && (
              <span className="text-xs text-red-500">
                {errors.defaultSendMessage.message}
              </span>
            )}
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
          type="submit"
          variant="primary"
          loading={isSubmitting}
          className="flex items-center justify-center gap-2"
        >
          <CheckCircle size={18} /> Save changes
        </Button>
        {saved && !error && (
          <span className="text-sm text-green-700">Saved.</span>
        )}
      </div>
    </form>
  );
}
