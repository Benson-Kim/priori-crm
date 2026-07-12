import { z } from "zod";

export const customerSchema = z
  .object({
    customerType: z.string().min(1, "Please select a customer type"),
    companyName: z.string().optional(),
    firstName: z.string().min(2, "First name must be at least 2 characters"),
    lastName: z.string().min(2, "Last name must be at least 2 characters"),
    email: z.email("Please enter a valid email address"),
    phone: z
      .string()
      .regex(/^[0-9]{9,10}$/, "Phone number must be between 9-10 digits"),
    website: z.url("Please enter a valid URL").optional().or(z.literal("")),
    vatNumber: z.string().optional(),
    currency: z.string().min(1, "Please select a currency"),
    address: z.string().min(5, "Address must be at least 5 characters"),
    address2: z.string().optional(),
    country: z.string().min(1, "Please select a country"),
    province: z.string().min(1, "Please select a province or state"),
    city: z.string().min(2, "City must be at least 2 characters"),
    postalCode: z.string().min(3, "Postal code must be at least 3 characters"),
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
  );

export type CustomerFormData = z.infer<typeof customerSchema>;
