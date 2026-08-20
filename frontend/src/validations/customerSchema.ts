import { z } from "zod";

export const customerSchema = z
  .object({
    customerType: z.string().min(1, "Please select a customer type"),
    companyName: z.string().optional(),
    firstName: z.string().min(2, "First name must be at least 2 characters"),
    lastName: z.string().min(2, "Last name must be at least 2 characters"),
    // Email, phone, website and postal code are optional. Each still
    // validates its format when filled in, so an empty field is accepted
    // but a malformed one is not.
    email: z
      .email("Please enter a valid email address")
      .optional()
      .or(z.literal("")),
    // Phone format depends on the selected country, so it is validated in
    // the superRefine below rather than here: Kenyan numbers are entered as
    // local digits behind the form's +254 decoration, every other country
    // takes full international (+…) input (#63/#64 convention).
    phone: z.string().optional().or(z.literal("")),
    website: z.url("Please enter a valid URL").optional().or(z.literal("")),
    vatNumber: z.string().optional(),
    currency: z.string().min(1, "Please select a currency"),
    address: z.string().min(5, "Address must be at least 5 characters"),
    address2: z.string().optional(),
    country: z.string().min(1, "Please select a country"),
    city: z.string().min(2, "City must be at least 2 characters"),
    postalCode: z
      .string()
      .min(3, "Postal code must be at least 3 characters")
      .optional()
      .or(z.literal("")),
  })
  .refine(
    (data) => {
      if (data.customerType === "business" && !data.companyName) {
        return false;
      }
      return true;
    },
    {
      message: "Company name is required for business customers",
      path: ["companyName"],
    }
  )
  // Phone is conditionally required: an individual is only reachable
  // directly, while a company is normally reached through a billing contact.
  // Mirrors the API's CustomerCreate.validate_individual_phone.
  .refine(
    (data) => {
      if (data.customerType === "individual" && !data.phone) {
        return false;
      }
      return true;
    },
    {
      message: "Phone number is required for individual customers.",
      path: ["phone"],
    }
  )
  // Country-aware phone format (#64): the form stores Kenyan numbers as the
  // 9-10 local digits (the +254 is input decoration and re-added on submit);
  // any other country's number is stored as typed and must be full
  // international E.164, which the API accepts verbatim for every country.
  .superRefine((data, ctx) => {
    if (!data.phone) return;
    if (data.country === "KE" || !data.country) {
      if (!/^[0-9]{9,10}$/.test(data.phone)) {
        ctx.addIssue({
          code: "custom",
          path: ["phone"],
          message: "Phone number must be between 9-10 digits",
        });
      }
      return;
    }
    const trimmed = data.phone.trim();
    const digits = trimmed.replace(/[^\d]/g, "");
    if (
      !trimmed.startsWith("+") ||
      digits.length < 8 ||
      digits.length > 15 ||
      !/^\+[\d ()-]+$/.test(trimmed)
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["phone"],
        message:
          "Enter the number in international format, e.g. +250 788 123 456",
      });
    }
  });

export type CustomerFormData = z.infer<typeof customerSchema>;
