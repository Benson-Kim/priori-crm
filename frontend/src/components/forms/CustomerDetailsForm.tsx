import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
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

const customerTypeOptions = [
    { value: "individual", label: "Individual" },
    { value: "business", label: "Business" },
];

const currencyOptions = [
    { value: "KES", label: "Kenyan Shilling (KES)" },
    { value: "USD", label: "US Dollar (USD)" },
    { value: "EUR", label: "Euro (EUR)" },
];

const countryOptions = [
    { value: "KE", label: "Kenya" },
    { value: "UG", label: "Uganda" },
    { value: "TZ", label: "Tanzania" },
];

const provinceOptions = [
    { value: "nairobi", label: "Nairobi" },
    { value: "coastal", label: "Coastal" },
    { value: "rift-valley", label: "Rift Valley" },
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
            province: "",
            city: "",
            postalCode: "",
        },
    });

    useEffect(() => {
        if (initialData) {
            reset(initialData);
        }
    }, [initialData, reset]);

    const onSubmit = (data: CustomerFormData) => {
        onSave?.(data);
    };

    const renderFieldError = (fieldError: { message?: string } | undefined) => {
        return fieldError ? (
            <p className="text-red-500 text-xs font-medium mt-1">
                {fieldError.message}
            </p>
        ) : null;
    };

    return (
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-8">
            {/* Customer Details Section */}
            <Card className="rounded-xl bg-white border-gray-300 p-6">
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
                    <div>
                        <label htmlFor="customerType" className="block text-sm font-semibold text-gray-900 mb-2">
                            Customer Type
                        </label>
                        <Controller
                            name="customerType"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Select
                                        {...field}
                                        id="customerType"
                                        options={customerTypeOptions}
                                        placeholder="Select customer type"
                                    />
                                    {renderFieldError(errors.customerType)}
                                </>
                            )}
                        />
                    </div>
                    <div>
                        <label htmlFor="companyName" className="block text-sm font-semibold text-gray-900 mb-2">
                            Company Name
                        </label>
                        <Controller
                            name="companyName"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="companyName"
                                        type="text"
                                        {...field}
                                        placeholder="Company name"
                                    />
                                    {renderFieldError(errors.companyName)}
                                </>
                            )}
                        />
                    </div>
                </div>

                {/* First Name & Last Name */}
                <div className="grid grid-cols-2 gap-6 mb-6">
                    <div>
                        <label
                            htmlFor="firstName"
                            className="block text-sm font-semibold text-gray-900 mb-2"
                        >
                            First Name
                        </label>

                        <Controller
                            name="firstName"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="firstName"
                                        type="text"
                                        {...field}
                                        placeholder="First name"
                                        error={errors.firstName?.message}
                                    />

                                    {renderFieldError(errors.firstName)}
                                </>
                            )}
                        />
                    </div>

                    <div>
                        <label
                            htmlFor="lastName"
                            className="block text-sm font-semibold text-gray-900 mb-2"
                        >
                            Last Name
                        </label>

                        <Controller
                            name="lastName"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="lastName"
                                        type="text"
                                        {...field}
                                        placeholder="Last name"
                                        error={errors.lastName?.message}
                                    />

                                    {renderFieldError(errors.lastName)}
                                </>
                            )}
                        />
                    </div>
                </div>

                {/* Email & Phone */}
                <div className="grid grid-cols-2 gap-6 mb-6">
                    <div>
                        <label htmlFor="email" className="block text-sm font-semibold text-gray-900 mb-2">
                            Email
                        </label>
                        <Controller
                            name="email"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="email"
                                        type="email"
                                        {...field}
                                        placeholder="email@example.com"
                                        error={errors.email ? "true" : undefined}
                                    />
                                    {renderFieldError(errors.email)}
                                </>
                            )}
                        />
                    </div>
                    <div>
                        <label htmlFor="phone" className="block text-sm font-semibold text-gray-900 mb-2">
                            Phone
                        </label>
                        <Controller
                            name="phone"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="phone"
                                        prefix="+254"
                                        type="tel"
                                        {...field}
                                        placeholder="7XX XXX XXX"
                                        error={errors.phone ? "true" : undefined}
                                    />
                                    {renderFieldError(errors.phone)}
                                </>
                            )}
                        />
                    </div>
                </div>

                {/* Website & VAT Number */}
                <div className="grid grid-cols-2 gap-6">
                    <div>
                        <label htmlFor="website" className="block text-sm font-semibold text-gray-900 mb-2">
                            Website
                        </label>
                        <Controller
                            name="website"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="website"
                                        type="url"
                                        {...field}
                                        placeholder="https://example.com"
                                        error={errors.website ? "true" : undefined}
                                    />
                                    {renderFieldError(errors.website)}
                                </>
                            )}
                        />
                    </div>
                    <div>
                        <label htmlFor="vatNumber" className="block text-sm font-semibold text-gray-900 mb-2">
                            VAT Number
                        </label>
                        <Controller
                            name="vatNumber"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="vatNumber"
                                        type="text"
                                        {...field}
                                        placeholder="Enter VAT number"
                                    />
                                    {renderFieldError(errors.vatNumber)}
                                </>
                            )}
                        />
                    </div>
                </div>
            </Card>

            {/* Billing Address Section */}
            <Card className="rounded-xl bg-white border-gray-300 p-6">
                {/* Section Header */}
                <h3 className="text-xl font-bold text-gray-800 mb-6">
                    Billing Address
                </h3>

                {/* Currency */}
                <div className="mb-6">
                    <Controller
                        name="currency"
                        control={control}
                        render={({ field }) => (
                            <>
                                <Select
                                    id="currency"
                                    {...field}
                                    options={currencyOptions}
                                    placeholder="Select currency"
                                />
                                {renderFieldError(errors.currency)}
                            </>
                        )}
                    />
                </div>

                {/* Address Fields */}
                <div className="grid grid-cols-2 gap-6 mb-6">
                    <div>
                        <Controller
                            name="address"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="address"
                                        type="text"
                                        {...field}
                                        placeholder="Address"
                                        error={errors.address ? "true" : undefined}
                                    />
                                    {renderFieldError(errors.address)}
                                </>
                            )}
                        />
                    </div>
                    <div>
                        <Controller
                            name="address2"
                            control={control}
                            render={({ field }) => (
                                <Input
                                    type="text"
                                    id="address2"
                                    {...field}
                                    placeholder="Address 2 (optional)"
                                />
                            )}
                        />
                    </div>
                </div>

                {/* Country & Province */}
                <div className="grid grid-cols-2 gap-6 mb-6">
                    <div>
                        <Controller
                            name="country"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Select
                                        id="country"
                                        {...field}
                                        options={countryOptions}
                                        placeholder="Country"
                                    />
                                    {renderFieldError(errors.country)}
                                </>
                            )}
                        />
                    </div>
                    <div>
                        <Controller
                            name="province"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Select
                                        id="province"
                                        {...field}
                                        options={provinceOptions}
                                        placeholder="Province/State/County"
                                    />
                                    {renderFieldError(errors.province)}
                                </>
                            )}
                        />
                    </div>
                </div>

                {/* City & Postal Code */}
                <div className="grid grid-cols-2 gap-6">
                    <div>
                        <Controller
                            name="city"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="city"
                                        type="text"
                                        {...field}
                                        placeholder="City"
                                        error={errors.city ? "true" : undefined}
                                    />
                                    {renderFieldError(errors.city)}
                                </>
                            )}
                        />
                    </div>
                    <div>
                        <Controller
                            name="postalCode"
                            control={control}
                            render={({ field }) => (
                                <>
                                    <Input
                                        id="postalCode"
                                        type="text"
                                        {...field}
                                        placeholder="Postal Code"
                                        error={errors.postalCode ? "true" : undefined}
                                    />
                                    {renderFieldError(errors.postalCode)}
                                </>
                            )}
                        />
                    </div>
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
