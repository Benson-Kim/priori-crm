import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { COUNTRIES, DEFAULT_COUNTRY } from "@/lib/countries";
import { customerStatusOptions, customerTypeOptions, type Currency, type CustomerStatus, type CustomerType } from "@/lib/enums";
import { createCustomer } from "@/services/customerApi";
import { checkEmailDuplicate } from "@/services/vendorApi";
import { CheckCircle, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { Card } from "../ui/Card";
import { Select } from "../ui/Select";

interface QuickCustomerForm {
    customerType: CustomerType;
    status: CustomerStatus;
    companyName?: string;
    firstName: string;
    lastName: string;
    email?: string;
    phone?: string;
    address: string;
    country: string;
    city: string;
    postalCode?: string;
    website?: string;
    vatNumber?: string;
}

interface AddCustomerModalProps {
    isOpen: boolean;
    onClose: () => void;
    onCustomerAdded: (customerId: string, customerName: string) => void;
}


export function AddCustomerModal({ isOpen, onClose, onCustomerAdded }: AddCustomerModalProps) {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Duplicate email state
    const [emailWarning, setEmailWarning] = useState<string | null>(null);
    const [checkingEmail, setCheckingEmail] = useState(false);


    const {
        register,
        handleSubmit,
        reset,
        control,
        formState: { errors },
    } = useForm<QuickCustomerForm>({
        defaultValues: {
            customerType: "individual" satisfies CustomerType,
            status: "active" satisfies CustomerStatus,
            companyName: "",
            firstName: "",
            lastName: "",
            email: "",
            phone: "",
            address: "",
            country: DEFAULT_COUNTRY,
            city: "",
            postalCode: "",
            website: "",
            vatNumber: "",
        },
    });

    const emailValue = useWatch({ name: "email", control });
    const customerTypeValue = useWatch({ name: "customerType", control });
    const phoneRequired = customerTypeValue === "individual";


    const onSubmit = async (data: QuickCustomerForm) => {
        try {
            setIsLoading(true);
            setError(null);

            // Transform to match backend API
            const customerData = {
                customerType: data.customerType,
                status: data.status,
                firstName: data.firstName,
                lastName: data.lastName,
                email: data.email || undefined,
                phone: data.phone || undefined,
                address: data.address,
                country: data.country,
                city: data.city,
                postalCode: data.postalCode || undefined,
                currency: "KES" satisfies Currency,
                website: data.website || undefined,
                vatNumber: data.vatNumber || undefined,
            };

            const customer = await createCustomer(customerData);
            const displayName = `${data.firstName} ${data.lastName}`;

            onCustomerAdded(customer.id, displayName);
            reset();
            onClose();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to add customer");
        } finally {
            setIsLoading(false);
        }
    };

    const handleClose = () => {
        reset();
        setError(null);
        onClose();
    };

    // Handle Duplicate Email Check
    useEffect(() => {
        const checkEmail = async () => {
            if (!emailValue || !emailValue.includes('@')) {
                setEmailWarning(null);
                return;
            }
            setCheckingEmail(true);
            try {
                const res = await checkEmailDuplicate(emailValue);
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
    }, [emailValue]);

    return (
        <Dialog isOpen={isOpen} onClose={handleClose} title="Customer">
            {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                    <p className="text-red-800 text-sm">{error}</p>
                </div>
            )}
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-8 max-h-[90vh]">
                <Card padding="lg" className="rounded-xl  flex flex-col gap-6">

                    {/* Business Type */}
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Customer Type <span className="text-red-500">*</span>
                            </label>
                            <Select id="customerType" options={customerTypeOptions}
                                {...register("customerType", { required: "Customer type is required" })}
                                placeholder="Select customer type"
                                error={errors.customerType?.message}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Status <span className="text-red-500">*</span>
                            </label>
                            <Select id="status" options={customerStatusOptions}
                                {...register("status", { required: "Customer status is required" })}
                                placeholder="Select status"
                                error={errors.status?.message}
                            />

                        </div>
                    </div>

                    {/* Full Name */}
                    <div>
                        <label className="block text-sm font-semibold text-gray-900 mb-2">
                            Full Name <span className="text-red-500">*</span>
                        </label>
                        <div className="grid grid-cols-2 gap-3">
                            <Input
                                {...register("firstName", { required: "First name is required" })}
                                placeholder="First name"
                                error={errors.firstName?.message}
                            />
                            <Input
                                {...register("lastName", { required: "Last name is required" })}
                                placeholder="Last name"
                                error={errors.lastName?.message}
                            />
                        </div>
                    </div>

                    {/* Email */}
                    <div>
                        <label className="block text-sm font-semibold text-gray-900 mb-2">
                            Email
                        </label>
                        <>
                            <Input
                                {...register("email", {
                                    pattern: {
                                        value: /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i,
                                        message: "Invalid email address"
                                    }
                                })}
                                type="email"
                                placeholder="email@example.com"
                                error={errors.email?.message}
                            />
                            {checkingEmail && <p className="text-xs text-gray-400 mt-1">Checking email...</p>}
                            {emailWarning && <p className="text-xs text-orange-500 mt-1">{emailWarning}</p>}
                        </>
                    </div>

                    {/* Phone */}
                    <div>
                        <label className="block text-sm font-semibold text-gray-900 mb-2">
                            Phone
                            {phoneRequired && <span className="text-red-500"> *</span>}
                        </label>
                        <Input
                            {...register("phone", {
                                validate: (value) =>
                                    !phoneRequired || !!value ||
                                    "Phone number is required for individual customers.",
                            })}
                            prefix="+254"
                            type="tel"
                            placeholder="700 000 000"
                            error={errors.phone?.message}
                        />
                    </div>

                    {/* Address */}
                    <div>
                        <label className="block text-sm font-semibold text-gray-900 mb-2">
                            Address <span className="text-red-500">*</span>
                        </label>
                        <Input
                            {...register("address", {
                                required: "Address is required",
                                minLength: { value: 5, message: "Address must be at least 5 characters" }
                            })}
                            placeholder="Street address"
                            error={errors.address?.message}
                        />
                    </div>

                    {/* City & Postal Code */}
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                City <span className="text-red-500">*</span>
                            </label>
                            <Input
                                {...register("city", {
                                    required: "City is required",
                                    minLength: { value: 2, message: "City must be at least 2 characters" }
                                })}
                                placeholder="City"
                                error={errors.city?.message}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Postal Code
                            </label>
                            <Input
                                {...register("postalCode", {
                                    minLength: { value: 3, message: "Postal code must be at least 3 characters" }
                                })}
                                placeholder="00100"
                                error={errors.postalCode?.message}
                            />
                        </div>
                    </div>

                    {/* Country */}
                    <div>
                        <label className="block text-sm font-semibold text-gray-900 mb-2">
                            Country <span className="text-red-500">*</span>
                        </label>
                        {/*
                          The backend contract (CustomerCreate.country) is an ISO
                          3166-1 alpha-2 code, so the option values are alpha-2
                          codes and only the labels are country names.
                        */}
                        <Select
                            id="country"
                            options={COUNTRIES}
                            {...register("country", { required: "Country is required" })}
                            error={errors.country?.message}
                        />
                    </div>

                    {/* Website and VAT Number */}
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                Website
                            </label>
                            <Input
                                {...register("website")}
                                type="url"
                                placeholder="https://example.com"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold text-gray-900 mb-2">
                                VAT Number
                            </label>
                            <Input
                                {...register("vatNumber")}
                                placeholder="AOE0387233N"
                            />
                        </div>
                    </div>

                    {/* Action Buttons */}
                </Card>
                <div className="flex gap-3">
                    <Button
                        type="button"
                        onClick={handleClose}
                        disabled={isLoading}
                        variant="outline"
                        className="flex-1 text-gray-600 border border-gray-600"
                    >
                        <X size={20} /> Cancel
                    </Button>
                    <Button
                        type="submit"
                        disabled={isLoading}
                        className="flex-1 font-semibold"
                    >
                        <CheckCircle size={20} /> {isLoading ? "Saving..." : "Save Customer"}
                    </Button>
                </div>
            </form>
        </Dialog >
    );
}


