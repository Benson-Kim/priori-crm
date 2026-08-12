/**
 * NewCompanyDialog
 *
 * The Companies screens carry a "+ New Company" button but the design does not
 * draw the form behind it. Both billing profiles are created unsynced, so a
 * new company lands straight in the "Needs sync" filter.
 */

import { useState } from "react";

import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import {
    BILLING_CURRENCIES,
    INDUSTRIES,
    createCompany,
    getSalesReps,
    type BillingCurrency,
} from "@/services/salesDeskApi";

interface NewCompanyDialogProps {
    isOpen: boolean;
    onClose: () => void;
    onCreated: () => void;
}

const EMPTY_FORM = {
    name: "",
    industry: INDUSTRIES[0],
    contact: "",
    email: "",
    phone: "",
    tenant: "",
    primaryCurrency: "USD" as BillingCurrency,
    ownerId: "",
};

export function NewCompanyDialog({
    isOpen,
    onClose,
    onCreated,
}: Readonly<NewCompanyDialogProps>) {
    const reps = getSalesReps();
    const [form, setForm] = useState({ ...EMPTY_FORM, ownerId: reps[0]?.id ?? "" });
    const [error, setError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);

    const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
        setForm((previous) => ({ ...previous, [key]: value }));

    const close = () => {
        setForm({ ...EMPTY_FORM, ownerId: reps[0]?.id ?? "" });
        setError(null);
        onClose();
    };

    const submit = async () => {
        setIsSaving(true);
        setError(null);
        try {
            // The service is the authority on what a valid company is.
            await createCompany(form);
            setForm({ ...EMPTY_FORM, ownerId: reps[0]?.id ?? "" });
            onCreated();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not register that company.");
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Dialog
            isOpen={isOpen}
            onClose={close}
            title="New company"
            description="Both USD and KES billing profiles are created unsynced. Push them to accounting once the commercial terms are agreed."
            confirmLabel="Register company"
            onConfirm={() => void submit()}
            isLoading={isSaving}
        >
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="sm:col-span-2">
                    <Label htmlFor="company-name">Company name</Label>
                    <Input
                        id="company-name"
                        value={form.name}
                        onChange={(event) => set("name", event.target.value)}
                        placeholder="Baraka Logistics"
                        autoComplete="organization"
                        required
                    />
                </div>

                <Select
                    id="company-industry"
                    label="Industry"
                    value={form.industry}
                    onChange={(event) => set("industry", event.target.value as typeof form.industry)}
                    options={INDUSTRIES.map((industry) => ({ value: industry, label: industry }))}
                />

                <Select
                    id="company-owner"
                    label="Owner"
                    value={form.ownerId}
                    onChange={(event) => set("ownerId", event.target.value)}
                    options={reps.map((rep) => ({ value: rep.id, label: rep.name }))}
                />

                <div>
                    <Label htmlFor="company-contact">Primary contact</Label>
                    <Input
                        id="company-contact"
                        value={form.contact}
                        onChange={(event) => set("contact", event.target.value)}
                        placeholder="Alice Wanjiru"
                        autoComplete="name"
                        required
                    />
                </div>

                <div>
                    <Label htmlFor="company-email">Email</Label>
                    <Input
                        id="company-email"
                        type="email"
                        value={form.email}
                        onChange={(event) => set("email", event.target.value)}
                        placeholder="alice@baraka.co.ke"
                        autoComplete="email"
                        required
                    />
                </div>

                <div>
                    <Label htmlFor="company-phone">Phone</Label>
                    <Input
                        id="company-phone"
                        type="tel"
                        value={form.phone}
                        onChange={(event) => set("phone", event.target.value)}
                        placeholder="+254 720 114 220"
                        autoComplete="tel"
                    />
                </div>

                <Select
                    id="company-currency"
                    label="Default billing currency"
                    value={form.primaryCurrency}
                    onChange={(event) =>
                        set("primaryCurrency", event.target.value as BillingCurrency)
                    }
                    options={BILLING_CURRENCIES.map((currency) => ({
                        value: currency,
                        label: currency,
                    }))}
                />

                <div className="sm:col-span-2">
                    <Label htmlFor="company-tenant">Microsoft tenant (optional)</Label>
                    <Input
                        id="company-tenant"
                        value={form.tenant}
                        onChange={(event) => set("tenant", event.target.value)}
                        placeholder="barakalogistics.onmicrosoft.com"
                        className="font-mono"
                    />
                </div>

                {error && (
                    <p role="alert" className="text-sm text-red-600 sm:col-span-2">
                        {error}
                    </p>
                )}
            </div>
        </Dialog>
    );
}
