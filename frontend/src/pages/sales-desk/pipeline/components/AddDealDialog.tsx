/**
 * AddDealDialog
 *
 * Opens a deal on a company that is already on the book. The pipeline could
 * previously only gain deals sideways — by promoting a prospect out of the
 * future pipeline — which left no way to record a deal that arrived directly.
 *
 * The prototype's own creation path (`engageProspect`) leaves product, seats
 * and currency unscoped because a promoted prospect has not been qualified
 * yet. A deal entered here has been, so those are asked for up front.
 *
 * Currency is the field with a real constraint behind it: the server only
 * accepts a currency the company already holds a billing profile in, so the
 * options are derived from the chosen company rather than offered as a fixed
 * USD/KES pair that would 422 on submit.
 */

import { useEffect, useMemo, useState } from "react";

import { Dialog } from "@/components/ui/Dialog";
import { InlineSelect } from "@/components/ui/InlineSelect";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { NewCompanyDialog } from "../../companies/components/NewCompanyDialog";
import { useSalesReps } from "@/hooks/useSalesReps";
import { plural } from "@/lib/utils";
import {
    createDeal,
    formatDeskMoney,
    getCompanyList,
    getPriceList,
    MIN_SEATS,
    type BillingCurrency,
    type CompanyRow,
    type PipelineDeal,
    type PriceListRow,
} from "@/services/salesDeskApi";

interface AddDealDialogProps {
    isOpen: boolean;
    onClose: () => void;
    /** Handed the created deal so the workspace can open it in the drawer. */
    onCreated: (deal: PipelineDeal) => void;
}

/** The catalogue prices per seat/year in USD, so a hint is only offered there. */
const LIST_PRICE_CURRENCY: BillingCurrency = "USD";

const emptyForm = {
    companyId: "",
    ownerId: "",
    product: "",
    seats: String(MIN_SEATS),
    value: "",
    currency: "" as BillingCurrency | "",
    note: "",
};

export function AddDealDialog({ isOpen, onClose, onCreated }: Readonly<AddDealDialogProps>) {
    const { reps } = useSalesReps();

    const [companies, setCompanies] = useState<CompanyRow[]>([]);
    const [catalog, setCatalog] = useState<PriceListRow[]>([]);
    const [form, setForm] = useState(emptyForm);
    const [error, setError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    /*
     * Whether the rep has picked an owner themselves. Until they do, the owner
     * follows the company; once they have, switching company must not
     * overwrite their choice.
     */
    const [ownerPicked, setOwnerPicked] = useState(false);
    const [isCreatingCompany, setIsCreatingCompany] = useState(false);

    const set = <K extends keyof typeof emptyForm>(key: K, value: (typeof emptyForm)[K]) =>
        setForm((previous) => ({ ...previous, [key]: value }));

    /*
     * The company and product lists are only needed once the dialog is on
     * screen, and they are re-read on each open so a company registered in
     * another tab of the desk is pickable without a reload.
     */
    useEffect(() => {
        if (!isOpen) return;
        let active = true;

        getCompanyList()
            .then((list) => {
                if (active) setCompanies(list.items);
            })
            .catch(() => {
                if (active) setError("Could not load the companies list.");
            });

        getPriceList()
            .then((rows) => {
                if (active) setCatalog(rows);
            })
            .catch(() => {
                /*
                 * The catalogue only drives a convenience picker and the list
                 * price hint. Losing it leaves the product free-typed, which
                 * is still a usable form, so it is not raised as an error.
                 */
            });

        return () => {
            active = false;
        };
    }, [isOpen]);

    const company = companies.find((row) => row.id === form.companyId) ?? null;

    /* The server validates against exactly this set, so the picker mirrors it. */
    const currencyOptions = useMemo(
        () =>
            (company?.profiles ?? []).map((profile) => ({
                value: profile.currency,
                label: profile.is_default
                    ? `${profile.currency} · default`
                    : profile.currency,
            })),
        [company]
    );

    /*
     * Picking a company settles two fields for the rep: the deal inherits the
     * company's account owner, and its currency falls to the company's primary
     * profile. Both stay editable, and each is re-derived when the company
     * changes — the owner unless the rep has chosen one, the currency
     * unconditionally when the new company holds no profile in it, since the
     * server would reject a currency carried over from the previous company.
     */
    useEffect(() => {
        if (!company) return;
        setForm((previous) => {
            const holdsCurrency = company.profiles.some(
                (profile) => profile.currency === previous.currency
            );
            return {
                ...previous,
                ownerId: ownerPicked ? previous.ownerId : company.owner_id,
                currency: holdsCurrency ? previous.currency : company.primary_currency,
            };
        });
    }, [company, ownerPicked]);

    const seats = Number(form.seats);
    const listedProduct = catalog.find((row) => row.name === form.product) ?? null;

    /*
     * Annual value at list price, which is the figure the rep is usually about
     * to type. Offered rather than applied: a deal is often discounted, and
     * overwriting a typed value would lose their number.
     */
    const listPriceArr =
        listedProduct && Number.isInteger(seats) && seats >= MIN_SEATS
            ? listedProduct.annual_per_seat * seats
            : null;
    const showListPrice = listPriceArr !== null && form.currency === LIST_PRICE_CURRENCY;

    /** The chosen currency's profile still has to reach accounting to quote on. */
    const chosenProfile = company?.profiles.find(
        (profile) => profile.currency === form.currency
    );

    const close = () => {
        setForm(emptyForm);
        setOwnerPicked(false);
        setError(null);
        onClose();
    };

    /**
     * Explicit checks rather than the `required` attributes the sibling
     * dialogs carry: `Dialog` renders no <form> and confirms through an
     * onClick, so native constraint validation never runs.
     */
    const validate = (): string | null => {
        if (!form.companyId) return "Pick the company this deal is for.";
        if (company && company.profiles.length === 0)
            return `${company.name} has no billing profile yet, so a deal cannot be priced against it. Add one from the Companies register first.`;
        if (!form.ownerId) return "Pick the rep who owns this deal.";
        if (!form.product.trim()) return "Name the product being sold.";
        if (!Number.isInteger(seats) || seats < MIN_SEATS)
            return `Seats must be a whole number, ${MIN_SEATS} or more.`;
        if (form.value.trim() === "" || !Number.isFinite(Number(form.value)) || Number(form.value) < 0)
            return "Enter the annual value of the deal.";
        if (!form.currency) return "Pick the currency this deal bills in.";
        return null;
    };

    const submit = async () => {
        const invalid = validate();
        if (invalid) {
            setError(invalid);
            return;
        }

        setIsSaving(true);
        setError(null);
        try {
            const deal = await createDeal({
                companyId: form.companyId,
                ownerId: form.ownerId,
                product: form.product.trim(),
                seats,
                value: Number(form.value),
                currency: form.currency as BillingCurrency,
                note: form.note,
            });
            setForm(emptyForm);
            setOwnerPicked(false);
            onCreated(deal);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not open that deal.");
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Dialog
            isOpen={isOpen}
            onClose={close}
            title="Add a deal"
            description="The deal opens at Activation against a company already on the register, and joins the pipeline's activity and ageing counts straight away."
            confirmLabel="Add deal"
            onConfirm={() => void submit()}
            isLoading={isSaving}
        >
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {/*
                 * Pick from the register, or register one here. A deal often
                 * arrives before the company exists on the book, and sending
                 * the rep to the Companies screen to create it would lose
                 * everything they had already typed into this form.
                 *
                 * The create action lives inside the picker, above the search
                 * — the same place `CustomerSelector` and `VendorSelector` put
                 * it — rather than as a second control beside the field.
                 */}
                <div className="sm:col-span-2">
                    <InlineSelect
                        variant="sales-desk"
                        id="deal-company"
                        label="Company"
                        placeholder="Pick a company"
                        searchable
                        value={form.companyId}
                        onChange={(value) => set("companyId", value)}
                        onAddNew={() => setIsCreatingCompany(true)}
                        addNewLabel="Add New Company"
                        options={companies.map((row) => ({
                            value: row.id,
                            label: row.name,
                        }))}
                    />
                </div>

                <InlineSelect
                    variant="sales-desk"
                    id="deal-owner"
                    label="Owner"
                    placeholder="Pick a rep"
                    value={form.ownerId}
                    onChange={(value) => {
                        setOwnerPicked(true);
                        set("ownerId", value);
                    }}
                    options={reps.map((rep) => ({ value: rep.id, label: rep.name }))}
                />

                <div>
                    <Label htmlFor="deal-product">Product</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="deal-product"
                        list="deal-product-catalog"
                        value={form.product}
                        onChange={(event) => set("product", event.target.value)}
                        placeholder="Microsoft 365 Business Premium"
                    />
                    {/*
                     * A datalist rather than a select: the catalogue covers the
                     * usual sale, but the desk also books products it has not
                     * listed, and the server takes any name.
                     */}
                    <datalist id="deal-product-catalog">
                        {catalog.map((row) => (
                            <option key={row.name} value={row.name} />
                        ))}
                    </datalist>
                </div>

                <div>
                    <Label htmlFor="deal-seats">Seats</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="deal-seats"
                        type="number"
                        inputMode="numeric"
                        min={MIN_SEATS}
                        step={1}
                        value={form.seats}
                        onChange={(event) => set("seats", event.target.value)}
                    />
                </div>

                <InlineSelect
                    variant="sales-desk"
                    id="deal-currency"
                    label="Currency"
                    placeholder={form.companyId ? "No billing profile" : "Pick a company first"}
                    disabled={currencyOptions.length === 0}
                    value={form.currency}
                    onChange={(value) => set("currency", value as BillingCurrency)}
                    options={currencyOptions}
                />

                <div className="sm:col-span-2">
                    <Label htmlFor="deal-value">Annual value (ARR)</Label>
                    <Input
                        wrapperClassName="h-control"
                        id="deal-value"
                        type="number"
                        inputMode="decimal"
                        min={0}
                        step={100}
                        value={form.value}
                        onChange={(event) => set("value", event.target.value)}
                        placeholder="21120"
                    />
                    {showListPrice && (
                        <p className="mt-1.5 text-xs text-sd-muted">
                            List price is{" "}
                            {formatDeskMoney(listPriceArr, LIST_PRICE_CURRENCY)}/yr for{" "}
                            {plural(seats, "seat")}.{" "}
                            <button
                                type="button"
                                className="font-semibold text-sd-brand underline-offset-2 hover:underline"
                                onClick={() => set("value", String(listPriceArr))}
                            >
                                Use it
                            </button>
                        </p>
                    )}
                </div>

                <div className="sm:col-span-2">
                    <Label htmlFor="deal-note">Opening note</Label>
                    <textarea
                        id="deal-note"
                        rows={3}
                        value={form.note}
                        onChange={(event) => set("note", event.target.value)}
                        placeholder="Inbound from the Nairobi event. Wants Business Premium for 80 users, budget confirmed."
                        className="mt-1.5 w-full resize-y rounded-lg border border-gray-300 bg-gray-50 px-3 py-3 text-base text-gray-900 placeholder:text-gray-400"
                    />
                </div>

                {/*
                 * Not a blocker — the server opens the deal regardless — but
                 * quoting against an unsynced profile fails later, so it is
                 * worth knowing at the point the currency is chosen.
                 */}
                {chosenProfile && !chosenProfile.synced && (
                    <p className="text-xs text-sd-warn sm:col-span-2">
                        {company?.name}'s {chosenProfile.currency} profile has not been
                        pushed to accounting yet. The deal will open, but it cannot be
                        quoted until the profile is synced.
                    </p>
                )}

                {error && (
                    <p role="alert" className="text-sm text-red-600 sm:col-span-2">
                        {error}
                    </p>
                )}
            </div>

            {/*
             * Registered from here, the company is pushed straight into the
             * options and selected, so the rep returns to a filled-in field
             * rather than having to find what they just created. Its profiles
             * come back on the row, which is what the currency picker reads.
             */}
            <NewCompanyDialog
                isOpen={isCreatingCompany}
                onClose={() => setIsCreatingCompany(false)}
                onCreated={(created) => {
                    setCompanies((previous) => [created, ...previous]);
                    set("companyId", created.id);
                    setIsCreatingCompany(false);
                }}
            />
        </Dialog>
    );
}
