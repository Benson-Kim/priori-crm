/**
 * VendorModal
 *
 * The dialog frame around `VendorDetailsForm`. The form itself lives in
 * `components/forms` so the same one backs both this and the `/vendors/add`
 * page, the way `CustomerDetailsForm` backs the customer page and
 * `AddCustomerModal`.
 *
 * This frame is what inline creation uses — the vendor selector on a purchase
 * order or expense opens it without leaving the document being edited — and
 * what the vendor detail page opens to edit. The list page's own "Add Vendor"
 * goes to the full page instead.
 */

import { Dialog } from "@/components/ui/Dialog";
import { VendorDetailsForm } from "@/components/forms/VendorDetailsForm";

interface VendorModalProps {
    isOpen: boolean;
    onClose: () => void;
    vendorId?: string | null;
    onSuccess: (id?: string, name?: string) => void;
}

export function VendorModal({ isOpen, onClose, vendorId, onSuccess }: VendorModalProps) {
    return (
        <Dialog
            isOpen={isOpen}
            onClose={onClose}
            title={vendorId ? "Edit Vendor" : "Add Vendor"}
        >
            {/*
             * `Dialog` renders nothing while closed, so the form mounts fresh
             * on each open. That is what clears the fields between one vendor
             * and the next, which the modal used to do by hand on `isOpen`.
             */}
            <VendorDetailsForm
                vendorId={vendorId}
                onSaved={(id, name) => {
                    onSuccess(id, name);
                    onClose();
                }}
            />
        </Dialog>
    );
}
