import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { currencyOptions, customerTypeOptions } from "@/lib/enums";
import { formatKenyanPhoneInput, getKenyanPhoneGhostText, normalizeKenyanPhoneNumber } from "@/lib/utils";
import { customerSchema, type CustomerFormData } from "@/validations/customerSchema";
import { zodResolver } from "@hookform/resolvers/zod";
import { ChevronLeft, Save } from "lucide-react";
import { useEffect } from "react";
import { Controller, useForm } from "react-hook-form";

interface CustomerDetailsFormProps {
    initialData?: Partial<CustomerFormData>;
    onCancel?: () => void;
    onSave?: (data: CustomerFormData) => void;
    isLoading?: boolean;
}

const countryOptions = [
    { value: "KE", label: "Kenya" },
    { value: "UG", label: "Uganda" },
    { value: "TZ", label: "Tanzania" },
];

export function CustomerDetailsForm({
    initialData,
    onCancel,
    onSave,
    isLoading = false,
}: Readonly<CustomerDetailsFormProps>) {
    const {
        control,
        handleSubmit,
        reset,
        watch,
        formState: { errors },
    } = useForm<CustomerFormData>({
        resolver: zodResolver(customerSchema),
        defaultValues: initialData || {
            customerType: "",
            companyName: "",
            firstName: "",
            lastName: "",
            email: "",
            phone: "",
            website: "",
            vatNumber: "",
            currency: "",
            address: "",
            address2: "",
            country: "",
            city: "",
            postalCode: "",
        },
    });

    useEffect(() => {
        if (initialData) {
            reset(initialData);
        }
    }, [initialData, reset]);

    const customerType = watch("customerType");

    const onSubmit = (data: CustomerFormData) => {
        onSave?.({
            ...data,
            phone: normalizeKenyanPhoneNumber(data.phone ?? ""),
        });
    };

    function Field({ id, label, children, }: Readonly<{
        id: string; label: string; children: React.ReactNode;
    }>) {
        return (
            <div className="flex flex-col gap-2">
                <label htmlFor={id} className="text-sm font-semibold leading-6 text-gray-900">
                    {label}
                </label>
                {children}
            </div>
        );
    }

    return (
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-8">
            {/* Customer Details Section */}
            <Card className="rounded-xl bg-white border-gray-300 p-6 space-y-6">
                {/* Header */}
                <div className="flex items-center gap-3 mb-6">
                    <button
                        type="button"
                        onClick={onCancel}
                        className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors"
                    >
                        <ChevronLeft className="w-5 h-5" />
                    </button>
                    <h2 className="text-xl font-bold text-gray-900">Customer Details</h2>
                </div>

                {/* Customer Type & Company Name */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 mb-6">

                    <Field id="customerType" label="Customer Type">
                        <Controller
                            name="customerType"
                            control={control}
                            render={({ field }) => (
                                <Select
                                    id="customerType"
                                    {...field}
                                    options={customerTypeOptions}
                                    placeholder="Select customer type"
                                    error={errors.customerType?.message}
                                />
                            )}
                        />
                    </Field>
                    <div className="flex flex-col gap-2">
                        <label htmlFor="companyName" className="text-sm font-semibold leading-6 text-gray-900">
                            Company Name{customerType === "business" && <span className="text-red-500 ml-1">*</span>}
                        </label>
                        <Controller
                            name="companyName"
                            control={control}
                            render={({ field }) => (
                                <Input
                                    id="companyName"
                                    type="text"
                                    {...field}
                                    placeholder="Company name"
                                    error={errors.companyName?.message}
                                />
                            )}
                        />
                    </div>
                </div>

                {/* First Name & Last Name */}
                <div className="grid grid-cols-2 gap-6">

                    <Field id="firstName" label="First Name">
                        <Controller
                            name="firstName"
                            control={control}
                            render={({ field }) => (
                                <Input
                                    id="firstName"
                                    type="text"
                                    {...field}
                                    placeholder="First name"
                                    error={errors.firstName?.message}
                                />
                            )}
                        />
                    </Field>
                    <Field id="lastName" label="Last Name">
                        <Controller
                            name="lastName"
                            control={control}
                            render={({ field }) => (
                                <Input
                                    id="lastName"
                                    type="text"
                                    {...field}
                                    placeholder="Last name"
                                    error={errors.lastName?.message}
                                />
                            )}
                        />
                    </Field>

                </div>

                {/* Email & Phone */}
                <div className="grid grid-cols-2 gap-6 mb-6">

                    <Field id="email" label="Email">
                        <Controller
                            name="email"
                            control={control}
                            render={({ field }) => (
                                <Input
                                    id="email"
                                    type="email"
                                    {...field}
                                    placeholder="email@example.com"
                                    error={errors.email?.message}
                                />
                            )}
                        />
                    </Field>
                    <Field id="phone" label="Phone">
                        <Controller
                            name="phone"
                            control={control}
                            render={({ field }) => {
                                const formattedValue = formatKenyanPhoneInput(field.value ?? "");

                                return (
                                    <Input
                                        id="phone"
                                        prefix="+254"
                                        type="tel"
                                        name={field.name}
                                        ref={field.ref}
                                        value={formattedValue}
                                        ghostText={getKenyanPhoneGhostText(formattedValue)}
                                        onBlur={field.onBlur}
                                        onChange={(event) => {
                                            const digitsOnly = event.target.value.replace(/\D/g, "");

                                            let localDigits = digitsOnly;

                                            if (localDigits.startsWith("254")) {
                                                localDigits = localDigits.slice(3);
                                            }

                                            if (localDigits.startsWith("0")) {
                                                localDigits = localDigits.slice(1);
                                            }

                                            field.onChange(localDigits.slice(0, 9));
                                        }}
                                        placeholder=""
                                        error={errors.phone?.message}
                                    />
                                );
                            }}
                        />
                    </Field>
                </div>


                {/* Currency & VAT Number */}
                <div className="grid grid-cols-2 gap-6">
                    <Field id="currency" label="Currency">
                        <Controller
                            name="currency"
                            control={control}
                            render={({ field }) => (
                                <Select
                                    id="currency"
                                    {...field}
                                    options={currencyOptions}
                                    placeholder="Select currency"
                                    error={errors.currency?.message}
                                />
                            )}
                        />
                    </Field>

                    <Field id="vatNumber" label="VAT Number">
                        <Controller
                            name="vatNumber"
                            control={control}
                            render={({ field }) => (
                                <Input
                                    id="vatNumber"
                                    type="text"
                                    {...field}
                                    placeholder="Enter VAT number"
                                    error={errors.vatNumber?.message}
                                />
                            )}
                        />
                    </Field>


                </div>
                {/* Website  */}
                <Field id="website" label="Website">
                    <Controller
                        name="website"
                        control={control}
                        render={({ field }) => (
                            <Input
                                id="website"
                                type="url"
                                {...field}
                                placeholder="https://example.com"
                                error={errors.website?.message}
                            />
                        )}
                    />
                </Field>
            </Card>

            {/* Billing Address Section */}
            <Card className="rounded-xl bg-white border-gray-300 p-6">
                {/* Section Header */}
                <h3 className="text-xl font-bold text-gray-800 mb-6">
                    Billing Address
                </h3>

                {/* Address Fields */}
                <div className="grid grid-cols-2 gap-6 mb-6">
                    <Controller
                        name="address"
                        control={control}
                        render={({ field }) => (
                            <Input
                                id="address"
                                type="text"
                                {...field}
                                placeholder="Address"
                                error={errors.address?.message}
                            />
                        )}
                    />
                    <Controller
                        name="address2"
                        control={control}
                        render={({ field }) => (
                            <Input
                                id="address2"
                                type="text"
                                {...field}
                                placeholder="Address 2 (optional)"
                                error={errors.address2?.message}
                            />
                        )}
                    />
                </div>

                {/* Country */}
                <div className="grid grid-cols-2 gap-6 mb-6">
                    <Controller
                        name="country"
                        control={control}
                        render={({ field }) => (
                            <Select
                                id="country"
                                {...field}
                                options={countryOptions}
                                placeholder="Select country"
                                error={errors.country?.message}
                            />
                        )}
                    />
                </div>

                {/* City & Postal Code */}
                <div className="grid grid-cols-2 gap-6">
                    <Controller
                        name="city"
                        control={control}
                        render={({ field }) => (
                            <Input
                                id="city"
                                type="text"
                                {...field}
                                placeholder="City"
                                error={errors.city?.message}
                            />
                        )}
                    />
                    <Controller
                        name="postalCode"
                        control={control}
                        render={({ field }) => (
                            <Input
                                id="postalCode"
                                type="text"
                                {...field}
                                placeholder="Postal Code"
                                error={errors.postalCode?.message}
                            />
                        )}
                    />
                </div>
            </Card>

            {/* Action Buttons */}
            <div className="flex gap-6">
                <Button
                    type="button"
                    onClick={onCancel}
                    disabled={isLoading}
                    variant="outline"
                    className="flex-1 px-3 py-4 border border-priori-purple/50 text-priori-purple font-bold text-lg rounded-lg hover:bg-priori-purple/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    Cancel
                </Button>
                <Button
                    type="submit"
                    disabled={isLoading}
                    className="flex-1 px-3 py-4 bg-priori-purple text-white font-bold text-lg rounded-lg hover:bg-priori-purple/90 transition-colors flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    <Save className="w-5 h-5" />
                    {isLoading ? "Saving..." : "Save"}
                </Button>
            </div>
        </form>
    );
}
