/**
 * CompanyDrawer
 *
 * Two billing profiles per company, USD and KES, each with its own terms, tax
 * treatment and credit ceiling. The dot on each tab shows whether that profile
 * has reached accounting, so an out-of-sync profile is visible without opening
 * it.
 *
 * The rule this panel enforces: editing terms invalidates whatever accounting
 * is holding, so any change clears the sync flag and the profile has to be
 * pushed again.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { cn, formatDate } from "@/lib/utils";
import {
    BILLING_CURRENCIES,
    PAYMENT_TERMS,
    TAX_TREATMENTS,
    formatDeskMoneyCompact,
    type BillingCurrency,
    type CompanyDetail,
    type CompanyDealSummary,
} from "@/services/salesDeskApi";
import { DeskDrawer, DeskSectionLabel } from "../../components/DeskDrawer";
import { RepAvatar } from "../../components/RepAvatar";
import { SyncStatus } from "../../components/SyncStatus";
import { StageBar } from "../../pipeline/components/StageBar";
import { CurrencyChip } from "./CurrencyChip";

interface CompanyDrawerProps {
    detail: CompanyDetail;
    onClose: () => void;
    /** Persist a terms change. Clears the profile's sync flag server-side. */
    onEditProfile: (
        currency: BillingCurrency,
        patch: { terms?: string; tax?: string; credit_limit?: number }
    ) => Promise<void>;
    /** Push one profile to accounting. */
    onSyncProfile: (currency: BillingCurrency) => Promise<void>;
    /** Push both profiles to accounting. */
    onSyncBoth: () => Promise<void>;
}

/** One labelled fact in the contact grid. */
function Fact({ label, value }: Readonly<{ label: string; value: string }>) {
    return (
        <div className="min-w-0">
            <DeskSectionLabel>{label}</DeskSectionLabel>
            <p className="pt-0.5 text-xs break-words text-desk-ink">{value}</p>
        </div>
    );
}

/** A company's deal, as the drawer's mini card. */
function DealCard({ deal }: Readonly<{ deal: CompanyDealSummary }>) {
    return (
        <div className="rounded-xl bg-desk-bg p-3">
            <div className="flex items-start justify-between gap-2">
                <p className="text-xs font-semibold text-desk-ink">{deal.product}</p>
                <span className="flex shrink-0 items-center gap-1.5">
                    <CurrencyChip currency={deal.billing_currency} />
                    <span className="text-xs font-bold text-desk-ink">
                        {formatDeskMoneyCompact(deal.value)}/yr
                    </span>
                </span>
            </div>
            <div className="pt-2">
                <StageBar deal={deal} />
            </div>
            <p className="pt-1 text-[11px] text-desk-muted">
                {deal.status === "open"
                    ? `${deal.stage_label} · ${deal.age_days}d in pipeline`
                    : `${deal.stage_label} · ${deal.age_days}d total`}
            </p>
        </div>
    );
}

export function CompanyDrawer({
    detail,
    onClose,
    onEditProfile,
    onSyncProfile,
    onSyncBoth,
}: Readonly<CompanyDrawerProps>) {
    const { company, deals } = detail;

    const [currency, setCurrency] = useState<BillingCurrency>(company.primary_currency);
    const [error, setError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);

    const profile =
        company.profiles.find((entry) => entry.currency === currency) ?? company.profiles[0];

    /*
     * Terms and tax write through on change so the sync flag drops the moment
     * a profile is edited. Only the credit limit is held locally while it is
     * being typed; committing on blur avoids a write per keystroke.
     */
    const [creditDraft, setCreditDraft] = useState(String(Math.round(profile.credit_limit)));
    const [draftKey, setDraftKey] = useState(profile.code);
    if (draftKey !== profile.code) {
        setDraftKey(profile.code);
        setCreditDraft(String(Math.round(profile.credit_limit)));
        setError(null);
    }

    const run = async (action: () => Promise<void>) => {
        setIsSaving(true);
        setError(null);
        try {
            await action();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save that.");
        } finally {
            setIsSaving(false);
        }
    };

    const commitCreditLimit = () => {
        const creditLimit = Number(creditDraft);
        if (!Number.isFinite(creditLimit) || creditLimit < 0) {
            setError("Credit limit must be a positive number.");
            return;
        }
        if (Math.round(profile.credit_limit) === Math.round(creditLimit)) return;
        void run(() => onEditProfile(currency, { credit_limit: creditLimit }));
    };

    return (
        <DeskDrawer
            label={`${company.name} billing profiles`}
            onClose={onClose}
            header={
                <>
                    <h2 className="text-[13px] font-semibold text-desk-ink">{company.name}</h2>
                    <p className="pt-0.5 text-[11px] text-desk-muted">
                        {company.industry} · registered {formatDate(company.registered_on)}
                    </p>
                    <p className="flex items-center gap-2 pt-2">
                        <RepAvatar
                            initials={company.owner_initials}
                            color={company.owner_color}
                            size="sm"
                        />
                        <span className="text-xs text-desk-ink">{company.owner_name}</span>
                    </p>
                </>
            }
        >
            {/* Who to call */}
            <div className="grid grid-cols-2 gap-4">
                <Fact label="Contact" value={company.contact} />
                <Fact label="Email" value={company.email} />
                <Fact label="Phone" value={company.phone} />
                <div className="min-w-0">
                    <DeskSectionLabel>Tenant</DeskSectionLabel>
                    <p className="pt-0.5 font-mono text-xs break-all text-desk-ink">
                        {company.tenant ?? "—"}
                    </p>
                </div>
            </div>

            {/* Billing profiles */}
            <div className="border-t border-desk-border pt-4">
                <DeskSectionLabel>Billing profiles</DeskSectionLabel>

                <div
                    role="tablist"
                    aria-label="Billing profile currency"
                    className="flex gap-2 pt-2"
                >
                    {BILLING_CURRENCIES.map((code) => {
                        const entry = company.profiles.find((item) => item.currency === code);
                        const isActive = code === currency;
                        return (
                            <button
                                key={code}
                                role="tab"
                                type="button"
                                aria-selected={isActive}
                                onClick={() => setCurrency(code)}
                                className={cn(
                                    "flex flex-1 items-center justify-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold transition-colors",
                                    isActive
                                        ? "bg-priori-purple text-white"
                                        : "border border-desk-border text-desk-ink hover:bg-desk-bg"
                                )}
                            >
                                {code} profile
                                <span
                                    className={cn(
                                        "size-2 rounded-full",
                                        entry?.synced ? "bg-desk-green" : "bg-gray-400"
                                    )}
                                    aria-hidden="true"
                                />
                                <span className="sr-only">
                                    {entry?.synced ? "in accounting" : "not synced"}
                                </span>
                            </button>
                        );
                    })}
                </div>

                <div className="flex items-center justify-between gap-2 pt-3">
                    <span className="flex items-center gap-2">
                        <span className="font-mono text-[13px] font-bold text-desk-ink">
                            {profile.code}
                        </span>
                        {profile.is_default && (
                            <span className="rounded-full bg-desk-accent-soft px-2 py-0.5 text-[10px] font-semibold text-priori-purple">
                                Default
                            </span>
                        )}
                    </span>
                    <SyncStatus synced={profile.synced} />
                </div>

                <div className="flex flex-col gap-3 pt-3">
                    <Select
                        id="profile-terms"
                        label="Payment terms"
                        value={profile.terms}
                        disabled={isSaving}
                        onChange={(event) =>
                            void run(() => onEditProfile(currency, { terms: event.target.value }))
                        }
                        options={PAYMENT_TERMS.map((term) => ({ value: term, label: term }))}
                        wrapperClassName="py-2 bg-white"
                        className="text-xs"
                    />

                    <Select
                        id="profile-tax"
                        label="Tax treatment"
                        value={profile.tax}
                        disabled={isSaving}
                        onChange={(event) =>
                            void run(() => onEditProfile(currency, { tax: event.target.value }))
                        }
                        options={TAX_TREATMENTS.map((tax) => ({ value: tax, label: tax }))}
                        wrapperClassName="py-2 bg-white"
                        className="text-xs"
                    />

                    <div>
                        <Label htmlFor="profile-credit">Credit limit ({currency})</Label>
                        <Input
                            id="profile-credit"
                            type="number"
                            inputMode="numeric"
                            min={0}
                            step={1000}
                            value={creditDraft}
                            onChange={(event) => setCreditDraft(event.target.value)}
                            onBlur={commitCreditLimit}
                            error={error ?? undefined}
                            wrapperClassName="bg-white"
                            className="py-2 text-xs"
                        />
                    </div>
                </div>

                <p className="pt-2 text-[11px] text-desk-muted" role="note">
                    Editing a profile marks it out of sync until you push it again.
                </p>

                <div className="flex items-center gap-2 pt-3">
                    <Button
                        variant="primary"
                        size="sm"
                        loading={isSaving}
                        disabled={profile.synced}
                        title={profile.synced ? "This profile is already in accounting" : undefined}
                        onClick={() => void run(() => onSyncProfile(currency))}
                        className="flex-1"
                    >
                        Re-sync {currency} profile
                    </Button>
                    <Button
                        variant="outline-secondary"
                        size="sm"
                        disabled={isSaving}
                        onClick={() => void run(onSyncBoth)}
                    >
                        Sync both
                    </Button>
                </div>
            </div>

            {/* Deals against this company */}
            <div className="border-t border-desk-border pt-4">
                <DeskSectionLabel>Deals</DeskSectionLabel>
                <div className="flex flex-col gap-2 pt-2">
                    {deals.length === 0 ? (
                        <p className="text-[11px] text-desk-muted">
                            No deals raised against this company yet.
                        </p>
                    ) : (
                        deals.map((deal) => <DealCard key={deal.id} deal={deal} />)
                    )}
                </div>
            </div>
        </DeskDrawer>
    );
}
