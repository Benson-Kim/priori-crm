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
    phone: z
      .string()
      .regex(/^[0-9]{9,10}$/, "Phone number must be between 9-10 digits")
      .optional()
      .or(z.literal("")),
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
  );

export type CustomerFormData = z.infer<typeof customerSchema>;
