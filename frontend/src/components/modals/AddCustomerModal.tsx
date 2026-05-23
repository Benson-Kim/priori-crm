import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { createCustomer } from "@/services/customerApi";
import { Check, X } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";

interface QuickCustomerForm {
    firstName: string;
    lastName: string;
    email: string;
    phone: string;
    address: string;
    country: string;
    city: string;
    province: string;
    postalCode: string;
    website?: string;
    vatNumber?: string;
}

interface AddCustomerModalProps {
    isOpen: boolean;
    onClose: () => void;
    onCustomerAdded: (customerId: string, customerName: string) => void;
}

const countryOptions = [
    { value: "KE", label: "Kenya" },
    { value: "UG", label: "Uganda" },
    { value: "TZ", label: "Tanzania" },
];

export function AddCustomerModal({ isOpen, onClose, onCustomerAdded }: AddCustomerModalProps) {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const {
        register,
        handleSubmit,
        reset,
        formState: { errors },
    } = useForm<QuickCustomerForm>({
        defaultValues: {
            firstName: "",
            lastName: "",
            email: "",
            phone: "",
            address: "",
            country: "KE",
            city: "",
            province: "",
            postalCode: "",
            website: "",
            vatNumber: "",
        },
    });

    const onSubmit = async (data: QuickCustomerForm) => {
        try {
            setIsLoading(true);
            setError(null);

            // Transform to match backend API
            const customerData = {
                customerType: "individual",
                firstName: data.firstName,
                lastName: data.lastName,
                email: data.email,
                phone: data.phone,
                address: data.address,
                country: data.country,
                city: data.city,
                province: data.province,
                postalCode: data.postalCode,
                currency: "KES",
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

    return (
        <Dialog isOpen={isOpen} onClose={handleClose} title="Customer">
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
                {error && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                        <p className="text-red-800 text-sm">{error}</p>
                    </div>
                )}

                {/* Add New Customer Button */}
                <div className="p-4 border-2 border-priori-purple rounded-lg text-center">
                    <button
                        type="button"
                        className="text-priori-purple font-semibold text-base flex items-center justify-center gap-2 w-full"
                    >
                        <span className="text-2xl">+</span> Add New Customer
                    </button>
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
                        Email <span className="text-red-500">*</span>
                    </label>
                    <Input
                        {...register("email", {
                            required: "Email is required",
                            pattern: {
                                value: /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i,
                                message: "Invalid email address"
                            }
                        })}
                        type="email"
                        placeholder="email@example.com"
                        error={errors.email?.message}
                    />
                </div>

                {/* Phone */}
                <div>
                    <label className="block text-sm font-semibold text-gray-900 mb-2">
                        Phone <span className="text-red-500">*</span>
                    </label>
                    <Input
                        {...register("phone", { required: "Phone is required" })}
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

                {/* City, Province, Postal Code */}
                <div className="grid grid-cols-3 gap-3">
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
                            Province <span className="text-red-500">*</span>
                        </label>
                        <Input
                            {...register("province", {
                                required: "Province is required",
                                minLength: { value: 2, message: "Province must be at least 2 characters" }
                            })}
                            placeholder="Province"
                            error={errors.province?.message}
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-semibold text-gray-900 mb-2">
                            Postal Code <span className="text-red-500">*</span>
                        </label>
                        <Input
                            {...register("postalCode", {
                                required: "Postal code is required",
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
                    <select
                        {...register("country", { required: "Country is required" })}
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg outline-none focus:border-priori-purple"
                    >
                        {countryOptions.map(opt => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                    </select>
                    {errors.country && <p className="text-red-500 text-xs mt-1">{errors.country.message}</p>}
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
                <div className="flex gap-3 pt-4">
                    <Button
                        type="button"
                        onClick={handleClose}
                        disabled={isLoading}
                        variant="outline"
                        className="flex-1 px-4 py-3 border border-gray-300 text-gray-700 font-semibold rounded-lg hover:bg-gray-50 transition-colors flex items-center justify-center gap-2"
                    >
                        <X size={20} /> Cancel
                    </Button>
                    <Button
                        type="submit"
                        disabled={isLoading}
                        className="flex-1 px-4 py-3 bg-priori-purple text-white font-semibold rounded-lg hover:bg-priori-purple/90 transition-colors flex items-center justify-center gap-2"
                    >
                        <Check size={20} /> {isLoading ? "Saving..." : "Save Customer"}
                    </Button>
                </div>
            </form>
        </Dialog>
    );
}


