/**
 * Settings form schemas (Organisation + Documents tabs).
 *
 * Converges the Settings pages on the react-hook-form + zod pattern already
 * used by AddCustomerModal / CustomerDetailsForm .
 *
 * The rules mirror `OwnerProfileUpdate` in `api/app/modules/owner/schemas.py`
 * and deliberately go no further: the backend normalises rather than rejects
 * most of these fields, so adding a stricter client-side gate would block
 * saves that the API accepts today. Two cases worth calling out:
 *
 *  - `website` is length-checked only. The API's `_normalise_website` accepts
 *    a bare "example.com" and prefixes "https://", so `z.url()` — used in
 *    customerSchema, where the contract differs — would reject a value the
 *    server stores happily.
 *  - `phone` is length-checked only, for the same reason plus one of our own:
 *    this form seeds straight from `profile.phone` without stripping a
 *    country prefix, so a stored "+254700000000" must remain valid on a field
 *    the user never touched.
 */

import { z } from "zod";

// Mirror the backend caps (api/app/constants/settings_defaults.py).
export const MAX_DEFAULT_TERMS_LENGTH = 2000;
export const MAX_DEFAULT_SEND_MESSAGE_LENGTH = 5000;

export const organisationSchema = z.object({
  // full_name: str = Field(..., min_length=1, max_length=255) with a
  // blank-to-none before-validator, so whitespace alone is rejected too.
  fullName: z
    .string()
    .trim()
    .min(1, "Business name is required.")
    .max(255, "Business name must be at most 255 characters"),
  locationWatermark: z
    .string()
    .max(255, "Location must be at most 255 characters")
    .optional(),
  address: z
    .string()
    .max(5000, "Address must be at most 5000 characters")
    .optional(),
  email: z
    .email("Please enter a valid email address")
    .max(255, "Email must be at most 255 characters")
    .optional()
    .or(z.literal("")),
  phone: z
    .string()
    .max(30, "Phone number must be at most 30 characters")
    .optional(),
  taxPin: z
    .string()
    .max(50, "Tax ID/PIN must be at most 50 characters")
    .optional(),
  website: z
    .string()
    .max(255, "Website must be at most 255 characters")
    .optional(),
});

export type OrganisationFormData = z.infer<typeof organisationSchema>;

export const documentDefaultsSchema = z.object({
  // jurisdiction is an ISO 3166-1 alpha-2 code chosen from a fixed select,
  // or empty when the org has never set one.
  jurisdiction: z
    .string()
    .max(2, "Jurisdiction must be a 2-letter country code")
    .optional(),
  defaultTermsAndConditions: z
    .string()
    .max(
      MAX_DEFAULT_TERMS_LENGTH,
      `Terms & Conditions must be at most ${MAX_DEFAULT_TERMS_LENGTH} characters`
    )
    .optional(),
  defaultSendMessage: z
    .string()
    .max(
      MAX_DEFAULT_SEND_MESSAGE_LENGTH,
      `Send message must be at most ${MAX_DEFAULT_SEND_MESSAGE_LENGTH} characters`
    )
    .optional(),
});

export type DocumentDefaultsFormData = z.infer<typeof documentDefaultsSchema>;
