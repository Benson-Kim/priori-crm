/**
 * AddProspectDialog
 *
 * The Future Pipeline screens carry an "+ Add planned deal" button but the
 * design does not draw the form behind it. This collects the minimum a
 * prospect needs to be actionable later.
 */

import { useEffect, useState } from "react";

import { Dialog } from "@/components/ui/Dialog";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { addProspect } from "@/services/salesDeskApi";
import { useSalesReps } from "@/hooks/useSalesReps";

interface AddProspectDialogProps {
    isOpen: boolean;
    onClose: () => void;
    onCreated: () => void;
}

/** Default engage-by date: a quarter out, which is the usual nurture horizon. */
function defaultEngageOn(): string {
    const date = new Date();
    date.setDate(date.getDate() + 90);
    return date.toISOString().slice(0, 10);
}

export function AddProspectDialog({
    isOpen,
    onClose,
    onCreated,
}: Readonly<AddProspectDialogProps>) {
    const { reps } = useSalesReps();

    const emptyForm = () => ({
        company: "",
        contact: "",
        note: "",
        engageOn: defaultEngageOn(),
        estimatedArr: "",
        ownerId: reps[0]?.id ?? "",
    });

    const [form, setForm] = useState(emptyForm);
    const [error, setError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);

    // The roster arrives after first paint, so the default owner is applied
    // when it lands rather than read during render.
    useEffect(() => {
        const first = reps[0]?.id;
        if (first) setForm((previous) => (previous.ownerId ? previous : { ...previous, ownerId: first }));
    }, [reps]);

    const set = <K extends keyof ReturnType<typeof emptyForm>>(
        key: K,
        value: ReturnType<typeof emptyForm>[K]
    ) => setForm((previous) => ({ ...previous, [key]: value }));

    const close = () => {
        setForm(emptyForm());
        setError(null);
        onClose();
    };

    const submit = async () => {
        setIsSaving(true);
        setError(null);
        try {
            await addProspect({
                company: form.company,
                contact: form.contact,
                note: form.note,
                engageOn: form.engageOn,
                estimatedArr: Number(form.estimatedArr || 0),
                ownerId: form.ownerId,
            });
            setForm(emptyForm());
            onCreated();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not plan that prospect.");
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Dialog
            isOpen={isOpen}
            onClose={close}
            title="Plan a prospect"
            description="Prospects sit outside the pipeline until their engage-by date arrives, at which point they surface as due."
            confirmLabel="Add planned deal"
            onConfirm={() => void submit()}
            isLoading={isSaving}
        >
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="sm:col-span-2">
                    <Label htmlFor="prospect-company">Company</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="prospect-company"
                        value={form.company}
                        onChange={(event) => set("company", event.target.value)}
                        placeholder="Rift Valley Tea Co."
                        autoComplete="organization"
                        required
                    />
                </div>

                <div>
                    <Label htmlFor="prospect-contact">Contact</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="prospect-contact"
                        value={form.contact}
                        onChange={(event) => set("contact", event.target.value)}
                        placeholder="Daniel Kiprop"
                        autoComplete="name"
                    />
                </div>

                <InlineSelect
                    variant="sales-desk"
                    id="prospect-owner"
                    label="Owner"
                    value={form.ownerId}
                    onChange={(value) => set("ownerId", value)}
                    options={reps.map((rep) => ({ value: rep.id, label: rep.name }))}
                />

                <div>
                    <Label htmlFor="prospect-date">Engage on</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="prospect-date"
                        type="date"
                        value={form.engageOn}
                        onChange={(event) => set("engageOn", event.target.value)}
                        required
                    />
                </div>

                <div>
                    <Label htmlFor="prospect-arr">Estimated ARR (USD)</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="prospect-arr"
                        type="number"
                        inputMode="numeric"
                        min={0}
                        step={1000}
                        value={form.estimatedArr}
                        onChange={(event) => set("estimatedArr", event.target.value)}
                        placeholder="21120"
                    />
                </div>

                <div className="sm:col-span-2">
                    <Label htmlFor="prospect-note">Why are we waiting?</Label>
                    <textarea
                        id="prospect-note"
                        rows={3}
                        value={form.note}
                        onChange={(event) => set("note", event.target.value)}
                        placeholder="Budget opens new FY. Wants Business Premium for 80 users."
                        className="mt-1.5 w-full resize-y rounded-lg border border-gray-300 bg-gray-50 px-3 py-3 text-sm text-gray-900 placeholder:text-gray-400"
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
