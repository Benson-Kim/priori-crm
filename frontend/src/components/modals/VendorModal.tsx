import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { COUNTRY_OPTIONS, CURRENCY_OPTIONS } from "@/lib/constants";
import { cn } from "@/lib/utils";
import {
    checkEmailDuplicate,
    createVendor,
    getVendor,
    updateVendor,
} from "@/services/vendorApi";
import { vendorSchema, type VendorFormData } from "@/validations/vendorSchema";
import { zodResolver } from "@hookform/resolvers/zod";
import { Check } from "lucide-react";
import { startTransition, useCallback, useEffect, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { Select } from "../ui/Select";

interface VendorModalProps {
    isOpen: boolean;
    onClose: () => void;
    vendorId?: string | null;
    onSuccess: (id?: string, name?: string) => void;
}

export function VendorModal({ isOpen, onClose, vendorId, onSuccess }: VendorModalProps) {
    // Mode can be: 'new' (empty form), 'edit' (editing existing vendor)
    const [mode, setMode] = useState<'new' | 'edit'>('new');
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Duplicate email state
    const [emailWarning, setEmailWarning] = useState<string | null>(null);
    const [checkingEmail, setCheckingEmail] = useState(false);
    const currentVendorId = vendorId || undefined;

    const {
        handleSubmit,
        reset,
        control,
        formState: { errors },
    } = useForm<VendorFormData>({
        resolver: zodResolver(vendorSchema),
        defaultValues: {
            vendor_name: "",
            email: "",
            phone_primary: "",
            address: "",
            country: COUNTRY_OPTIONS[0].value,
            currency: CURRENCY_OPTIONS[0].value,
        },
    });

    const emailValue = useWatch({ name: "email", control });

    const loadVendorData = useCallback(async (id: string) => {
        setIsLoading(true);
        try {
            const data = await getVendor(id);
            reset({
                vendor_name: data.vendor_name,
                email: data.email || "",
                phone_primary: data.phone_primary || "",
                phone_secondary: data.phone_secondary || "",
                address: data.address || "",
                country: data.country || COUNTRY_OPTIONS[0].value,
                website: data.website || "",
                vat_number: data.vat_number || "",
                tax_id_pin: data.tax_id_pin || "",
                notes: data.notes || "",
                currency: data.currency || CURRENCY_OPTIONS[0].value,
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load vendor");
        } finally {
            setIsLoading(false);
        }
    }, [reset]);

    // Initialize state when modal opens
    useEffect(() => {
        if (isOpen) {
            startTransition(() => {
                if (vendorId) {
                    setMode('edit');
                } else {
                    reset();
                    setError(null);
                    setEmailWarning(null);
                }
            });
            if (vendorId) {
                void (async () => { await loadVendorData(vendorId); })();
            }
        }
    }, [isOpen, vendorId, reset, loadVendorData]);


    // Handle Duplicate Email Check
    useEffect(() => {
        const checkEmail = async () => {
            if (!emailValue || !emailValue.includes('@')) {
                setEmailWarning(null);
                return;
            }
            setCheckingEmail(true);
            try {
                const res = await checkEmailDuplicate(emailValue, currentVendorId);
                if (res.exists) {
                    setEmailWarning(res.message ?? null);
                } else {
                    setEmailWarning(null);
                }
            } catch (err) {
                console.error("Email check failed", err);
            } finally {
                setCheckingEmail(false);
            }
        };

        const timeoutId = setTimeout(checkEmail, 500);
        return () => clearTimeout(timeoutId);
    }, [emailValue, currentVendorId]);



    const onSubmit = async (data: VendorFormData) => {
        try {
            setIsLoading(true);
            setError(null);

            const payload: Partial<VendorFormData> & { contact_id?: string } = {
                vendor_name: data.vendor_name,
                email: data.email || undefined,
                phone_primary: data.phone_primary || undefined,
                phone_secondary: data.phone_secondary || undefined,
                address: data.address || undefined,
                country: data.country || undefined,
                website: data.website || undefined,
                vat_number: data.vat_number || undefined,
                tax_id_pin: data.tax_id_pin || undefined,
                notes: data.notes || undefined,
                currency: data.currency || undefined,
            };

            let savedVendor;
            if (mode === 'edit' && vendorId) {
                savedVendor = await updateVendor(vendorId, payload);
            } else {
                savedVendor = await createVendor(payload);
            }

            onSuccess(savedVendor.id, savedVendor.vendor_name);
            onClose();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to save vendor");
        } finally {
            setIsLoading(false);
        }
    };

    const renderFieldError = (fieldError: { message?: string } | undefined) => {
        return fieldError ? (
            <p className="text-red-500 text-xs font-medium mt-1">
                {fieldError.message}
            </p>
        ) : null;
    };

    const title = mode === 'edit' ? "Edit Vendor" : "Add Vendor";

    return (
        <Dialog isOpen={isOpen} onClose={onClose} title={title}>
            {error && (
                <div className="p-3 mb-4 bg-red-50 border border-red-200 rounded-lg">
                    <p className="text-red-800 text-sm">{error}</p>
                </div>
            )}

            {(mode === 'new' || mode === 'edit') && (
                <form onSubmit={handleSubmit(onSubmit)} className="space-y-8 max-h-[75vh] overflow-y-auto px-2 pb-4">
                    {/* Vendor Details Section */}
                    <Card className="rounded-xl bg-white border-gray-300 flex flex-col gap-6 p-6">

                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Vendor Name
                            </label>
                            <Controller
                                name="vendor_name"
                                control={control}
                                render={({ field }) => (
                                    <>
                                        <Input
                                            {...field}
                                            placeholder="Enter name"
                                            error={errors.vendor_name?.message}
                                        />
                                        {renderFieldError(errors.vendor_name)}
                                    </>
                                )}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Address
                            </label>
                            <Controller
                                name="address"
                                control={control}
                                render={({ field }) => (
                                    <>
                                        <Input
                                            {...field}
                                            placeholder="Street address"
                                            error={errors.address?.message}
                                        />
                                        {renderFieldError(errors.address)}
                                    </>
                                )}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Email
                            </label>
                            <Controller
                                name="email"
                                control={control}
                                render={({ field }) => (
                                    <>
                                        <Input
                                            {...field}
                                            type="email"
                                            placeholder="email@example.com"
                                            error={errors.email?.message}
                                        />
                                        {checkingEmail && <p className="text-xs text-gray-400 mt-1">Checking email...</p>}
                                        {emailWarning && <p className="text-xs text-orange-500 mt-1">{emailWarning}</p>}
                                        {renderFieldError(errors.email)}
                                    </>
                                )}
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-6">
                            <div>
                                <label className="block text-sm font-semibold text-gray-900 mb-2">
                                    Country <span className="text-red-500">*</span>
                                </label>
                                <Controller
                                    name="country"
                                    control={control}
                                    render={({ field }) => (
                                        <>
                                            <Select
                                                id="country"
                                                {...field}
                                                options={COUNTRY_OPTIONS}
                                                placeholder="Country"
                                            />
                                            {renderFieldError(errors.country)}
                                        </>
                                    )}
                                />
                            </div>
                            <div className="mb-6">
                                <label className="block text-sm font-semibold text-gray-900 mb-2">
                                    Currency <span className="text-red-500">*</span>
                                </label>
                                <Controller
                                    name="currency"
                                    control={control}
                                    render={({ field }) => (
                                        <>
                                            <Select
                                                id="currency"
                                                {...field}
                                                options={CURRENCY_OPTIONS}
                                                placeholder="Select currency"
                                            />
                                            {renderFieldError(errors.currency)}
                                        </>
                                    )}
                                />
                            </div>
                        </div>

                        {/* Phone Numbers */}
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Primary Phone
                            </label>
                            <Controller
                                name="phone_primary"
                                control={control}
                                render={({ field }) => (
                                    <>
                                        <Input
                                            {...field}
                                            type="tel"
                                            placeholder="+254 700 000 000"
                                            error={errors.phone_primary?.message}
                                        />
                                        {renderFieldError(errors.phone_primary)}
                                    </>
                                )}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Secondary Phone
                            </label>
                            <Controller
                                name="phone_secondary"
                                control={control}
                                render={({ field }) => (
                                    <>
                                        <Input
                                            {...field}
                                            type="tel"
                                            placeholder="+254 700 000 000"
                                            error={errors.phone_secondary?.message}
                                        />
                                        {renderFieldError(errors.phone_secondary)}
                                    </>
                                )}
                            />
                        </div>

                        {/* Website & Notes */}
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Website
                            </label>
                            <Controller
                                name="website"
                                control={control}
                                render={({ field }) => (
                                    <>
                                        <Input
                                            {...field}
                                            type="url"
                                            placeholder="https://example.com"
                                            error={errors.website?.message}
                                        />
                                        {renderFieldError(errors.website)}
                                    </>
                                )}
                            />
                        </div>

                        <div className="grid grid-cols-2 gap-6">
                            <div>
                                <label className="block text-sm font-semibold text-gray-900 mb-2">
                                    VAT Number
                                </label>
                                <Controller
                                    name="vat_number"
                                    control={control}
                                    render={({ field }) => (
                                        <>
                                            <Input
                                                {...field}
                                                placeholder="VAT Number"
                                                error={errors.vat_number?.message}
                                            />
                                            {renderFieldError(errors.vat_number)}
                                        </>
                                    )}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-semibold text-gray-900 mb-2">
                                    Tax ID / PIN
                                </label>
                                <Controller
                                    name="tax_id_pin"
                                    control={control}
                                    render={({ field }) => (
                                        <>
                                            <Input
                                                {...field}
                                                placeholder="Tax PIN"
                                                error={errors.tax_id_pin?.message}
                                            />
                                            {renderFieldError(errors.tax_id_pin)}
                                        </>
                                    )}
                                />
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Notes
                            </label>
                            <Controller
                                name="notes"
                                control={control}
                                render={({ field }) => (
                                    <>
                                        <textarea
                                            {...field}
                                            rows={1}
                                            className={cn(
                                                `flex items-center w-full py-4 px-3 gap-3 rounded-lg border bg-gray-50 transition-all min-h-[42px]`,
                                                error
                                                    ? `border-red-300 focus-within:border-red-500 focus-within:ring-red-100`
                                                    : `border-gray-300 focus-within:border-priori-purple focus-within:ring-priori-purple/10`)}
                                            placeholder="Internal notes..."
                                        />
                                        {renderFieldError(errors.notes)}
                                    </>
                                )}
                            />
                        </div>

                        {/* Action Buttons */}
                        <div className="flex gap-3 pt-4">

                            <Button
                                type="submit"
                                disabled={isLoading}
                                className="flex-1 px-4 py-3 bg-priori-purple text-white font-semibold rounded-lg hover:bg-priori-purple/90 transition-colors flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                <Check size={20} /> {isLoading ? "Saving..." : "Save Vendor"}
                            </Button>
                        </div>
                    </Card>
                </form>
            )}
        </Dialog>
    );
}
