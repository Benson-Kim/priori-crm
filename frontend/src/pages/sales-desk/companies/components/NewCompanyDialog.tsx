/**
 * NewCompanyDialog
 *
 * The Companies screens carry a "+ New Company" button but the design does not
 * draw the form behind it. Both billing profiles are created unsynced, so a
 * new company lands straight in the "Needs sync" filter.
 */

import { useEffect, useState } from "react";

import { Dialog } from "@/components/ui/Dialog";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { Input } from "@/components/ui/Input";
import { KenyanPhoneInput } from "@/components/ui/KenyanPhoneInput";
import { Label } from "@/components/ui/Label";
import { toInternationalKenyanPhone, toLocalKenyanDigits } from "@/lib/utils";
import {
    BILLING_CURRENCIES,
    INDUSTRIES,
    createCompany,
    type BillingCurrency,
    type CompanyRow,
} from "@/services/salesDeskApi";
import { useSalesReps } from "@/hooks/useSalesReps";

interface NewCompanyDialogProps {
    isOpen: boolean;
    onClose: () => void;
    /**
     * Handed the registered company, so a caller that opened this to fill a
     * field (the deal dialog's company picker) can select it straight away.
     * Callers that only need to refresh a list can ignore the argument.
     */
    onCreated: (company: CompanyRow) => void;
}

const EMPTY_FORM = {
    name: "",
    industry: INDUSTRIES[0] as string,
    firstName: "",
    lastName: "",
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
    const { reps } = useSalesReps();
    const [form, setForm] = useState({ ...EMPTY_FORM, ownerId: reps[0]?.id ?? "" });
    const [error, setError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);

    // The roster arrives after first paint, so the default owner is applied
    // when it lands rather than read during render.
    useEffect(() => {
        const first = reps[0]?.id;
        if (first) setForm((previous) => (previous.ownerId ? previous : { ...previous, ownerId: first }));
    }, [reps]);

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
            const company = await createCompany(form);
            setForm({ ...EMPTY_FORM, ownerId: reps[0]?.id ?? "" });
            onCreated(company);
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
                        wrapperClassName="h-control"
                        id="company-name"
                        value={form.name}
                        onChange={(event) => set("name", event.target.value)}
                        placeholder="Baraka Logistics"
                        autoComplete="organization"
                        required
                    />
                </div>

                <InlineSelect
                    variant="sales-desk"
                    id="company-industry"
                    label="Industry"
                    value={form.industry}
                    onChange={(value) => set("industry", value as typeof form.industry)}
                    options={INDUSTRIES.map((industry) => ({ value: industry, label: industry }))}
                />

                <InlineSelect
                    variant="sales-desk"
                    id="company-owner"
                    label="Owner"
                    value={form.ownerId}
                    onChange={(value) => set("ownerId", value)}
                    options={reps.map((rep) => ({ value: rep.id, label: rep.name }))}
                />

                <div>
                    <Label htmlFor="company-first-name">Contact first name</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="company-first-name"
                        value={form.firstName}
                        onChange={(event) => set("firstName", event.target.value)}
                        placeholder="Alice"
                        autoComplete="given-name"
                        required
                    />
                </div>

                <div>
                    <Label htmlFor="company-last-name">Contact last name</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="company-last-name"
                        value={form.lastName}
                        onChange={(event) => set("lastName", event.target.value)}
                        placeholder="Wanjiru"
                        autoComplete="family-name"
                        required
                    />
                </div>

                <div>
                    <Label htmlFor="company-email">Email</Label>
                    <Input
                        wrapperClassName="h-control"
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
                    {/*
                     * The desk registers Kenyan companies, and the field
                     * already advertised a `+254` number, so it stores that
                     * shape and formats as it is typed.
                     */}
                    <KenyanPhoneInput
                        id="company-phone"
                        value={toLocalKenyanDigits(form.phone)}
                        onChange={(local) => set("phone", toInternationalKenyanPhone(local))}
                    />
                </div>

                <InlineSelect
                    variant="sales-desk"
                    id="company-currency"
                    label="Default billing currency"
                    value={form.primaryCurrency}
                    onChange={(value) =>
set("primaryCurrency", value as BillingCurrency)
                    }
                    options={BILLING_CURRENCIES.map((currency) => ({
                        value: currency,
                        label: currency,
                    }))}
                />

                <div className="sm:col-span-2">
                    <Label htmlFor="company-tenant">Microsoft tenant (optional)</Label>
                    <Input
                        wrapperClassName="h-control"
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
