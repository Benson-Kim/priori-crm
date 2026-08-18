/**
 * Engage a prospect.
 *
 * "Start engaging" promotes a prospect into a customer plus an open deal at
 * Activation. A prospect records only a company, a contact name and a note,
 * but a customer needs an email and a phone number, so those are collected
 * here rather than invented. The contact name is split into first/last as a
 * starting point and stays editable.
 *
 * Product, seats and currency are deliberately not asked for: the server
 * opens the deal on the org's first catalogued product at one seat in the
 * customer's primary currency, and the rep adjusts them in the deal drawer.
 */

import { useEffect, useState } from "react";

import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import type { EngageCustomerInput, ProspectRow } from "@/services/salesDeskApi";

interface EngageProspectDialogProps {
    prospect: ProspectRow | null;
    isSaving: boolean;
    onClose: () => void;
    onConfirm: (customer: EngageCustomerInput) => void;
}

/** "Grace Wanjiru" -> ["Grace", "Wanjiru"]; a single word becomes the first name. */
function splitContact(contact: string): [string, string] {
    const parts = contact.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return ["", ""];
    if (parts.length === 1) return [parts[0], ""];
    return [parts[0], parts.slice(1).join(" ")];
}

const EMPTY: EngageCustomerInput = { firstName: "", lastName: "", email: "", phone: "" };

export function EngageProspectDialog({
    prospect,
    isSaving,
    onClose,
    onConfirm,
}: Readonly<EngageProspectDialogProps>) {
    const [form, setForm] = useState<EngageCustomerInput>(EMPTY);
    const [error, setError] = useState<string | null>(null);

    // Reseed whenever a different prospect is engaged, so the previous
    // prospect's contact never leaks into the next one's form.
    useEffect(() => {
        if (!prospect) return;
        const [firstName, lastName] = splitContact(prospect.contact);
        setForm({ ...EMPTY, firstName, lastName });
        setError(null);
    }, [prospect]);

    const set = <K extends keyof EngageCustomerInput>(
        key: K,
        value: EngageCustomerInput[K]
    ) => setForm((previous) => ({ ...previous, [key]: value }));

    const submit = () => {
        if (!form.firstName.trim()) return setError("A first name is required.");
        if (!form.lastName.trim()) return setError("A last name is required.");
        if (!form.email.trim()) return setError("An email address is required.");
        if (!form.phone.trim()) return setError("A phone number is required.");
        setError(null);
        onConfirm({
            firstName: form.firstName.trim(),
            lastName: form.lastName.trim(),
            email: form.email.trim(),
            phone: form.phone.trim(),
        });
    };

    return (
        <Dialog
            isOpen={prospect !== null}
            onClose={onClose}
            title={prospect ? `Engage ${prospect.company}` : "Engage"}
            description="Registers the company and opens a deal at Activation."
            confirmLabel={isSaving ? "Engaging..." : "Start engaging"}
            onConfirm={submit}
            isLoading={isSaving}
        >
            <div className="flex flex-col gap-4">
                {error && (
                    <p role="alert" className="text-dense-md text-red-600">
                        {error}
                    </p>
                )}

                <div className="grid grid-cols-2 gap-4">
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="engage-first-name">First name</Label>
                        <Input
                            id="engage-first-name"
                            value={form.firstName}
                            onChange={(event) => set("firstName", event.target.value)}
                        />
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="engage-last-name">Last name</Label>
                        <Input
                            id="engage-last-name"
                            value={form.lastName}
                            onChange={(event) => set("lastName", event.target.value)}
                        />
                    </div>
                </div>

                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="engage-email">Email</Label>
                    <Input
                        id="engage-email"
                        type="email"
                        value={form.email}
                        onChange={(event) => set("email", event.target.value)}
                    />
                </div>

                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="engage-phone">Phone</Label>
                    <Input
                        id="engage-phone"
                        value={form.phone}
                        onChange={(event) => set("phone", event.target.value)}
                    />
                </div>
            </div>
        </Dialog>
    );
}
