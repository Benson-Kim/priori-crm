/**
 * Add Vendor.
 *
 * The full-page counterpart to the vendor dialog, mirroring
 * `/customers/add`: capturing a vendor from the register is a task in its own
 * right and gets the whole page, while the dialog stays for creating one
 * inline without abandoning a half-written purchase order.
 */

import { useNavigate } from "react-router-dom";

import { VendorDetailsForm } from "@/components/forms/VendorDetailsForm";

export default function AddVendorPage() {
    const navigate = useNavigate();

    return (
        <VendorDetailsForm
            // Straight to the new vendor, which is what the rep came to build;
            // falling back to the register if the server sent no id.
            onSaved={(id) => navigate(id ? `/vendors/${id}` : "/vendors")}
            onCancel={() => navigate("/vendors")}
        />
    );
}
