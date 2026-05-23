import { z } from "zod";

export const vendorSchema = z.object({
  vendor_name: z.string().min(2, "Vendor name must be at least 2 characters"),
  email: z.email("Please enter a valid email address").optional().or(z.literal("")),
  phone_primary: z.string().optional(),
  phone_secondary: z.string().optional(),
  currency: z.string().min(1, "Please select a currency"),
  address: z.string().optional(),
  country: z.string().min(1, "Please select a country"),
  website: z.url("Please enter a valid URL").optional().or(z.literal("")),
  vat_number: z.string().optional(),
  tax_id_pin: z.string().optional(),
  notes: z.string().optional(),
});

export type VendorFormData = z.infer<typeof vendorSchema>;
