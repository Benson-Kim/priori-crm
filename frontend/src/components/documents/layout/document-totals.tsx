/**
 * DocumentTotals — Read/write modes controlled via restrictedMode.
 */

import { Divider } from "@/components/ui/Divider";
import { formatCurrency } from "@/lib/utils";
import { Trash } from "lucide-react";
import type { DocumentTotals } from "../utils";

interface DocumentTotalsProps {
    totals: DocumentTotals;
    currency: string;
    discountType: "amount" | "percentage" | null;
    discountValue: string;
    restrictedMode: boolean;
    onDiscountTypeChange: (type: "amount" | "percentage") => void;
    onDiscountValueChange: (value: string) => void;
    onDiscountRemove: () => void;
    onAddDiscount: () => void;
    vatEnabled?: boolean;
    vatLabel?: string;
    // Optional: for viewer mode with payment info
    amountPaid?: number;
    balanceDue?: number;
}

export function DocumentTotalsPanel({
    totals,
    currency,
    discountType,
    discountValue,
    restrictedMode,
    onDiscountTypeChange,
    onDiscountValueChange,
    onDiscountRemove,
    onAddDiscount,
    vatEnabled = false,
    vatLabel,
    amountPaid,
    balanceDue,
}: Readonly<DocumentTotalsProps>) {
    return (
        <div className="w-full flex justify-end">
            <div className="min-w-80 flex flex-col gap-4 text-gray-800">

                {/* Subtotal */}
                <div className="flex justify-between items-center font-bold">
                    <span>Subtotal</span>
                    <span>{formatCurrency(totals.subtotal, currency ?? "KES")}</span>
                </div>

                {/* Discount */}
                {!restrictedMode && discountType === null ? (
                    <button
                        type="button"
                        onClick={onAddDiscount}
                        className="text-priori-purple font-bold text-left hover:underline flex items-center"
                    >
                        + Add a discount
                    </button>
                ) : discountType !== null ? (
                    // Show discount row if discountType is set AND (in edit mode OR discount amount > 0)
                    (!restrictedMode || totals.discountAmount > 0) && (
                        <div className="flex justify-between items-center">
                            {restrictedMode ? (
                                // Viewer mode: Simple display
                                <>
                                    <span className="font-bold">
                                        Discount <span className="font-normal">({discountType === "percentage" ? `${discountValue}%` : "Fixed"})</span>
                                    </span>
                                    <span className="text-red-500 font-medium">
                                        -{formatCurrency(totals.discountAmount, currency ?? "KES")}
                                    </span>
                                </>
                            ) : (
                                // Edit mode: Interactive controls
                                <>
                                    <div className="flex items-center gap-2">
                                        <span className="font-bold">Discount</span>
                                        <select
                                            value={discountType}
                                            onChange={(e) =>
                                                onDiscountTypeChange(e.target.value as "amount" | "percentage")
                                            }
                                            className="bg-transparent border border-gray-300 rounded-lg px-3 py-3 text-sm outline-none cursor-pointer"
                                        >
                                            <option value="percentage">%</option>
                                            <option value="amount">Fixed</option>
                                        </select>
                                        <input
                                            type="number"
                                            value={discountValue}
                                            onChange={(e) => onDiscountValueChange(e.target.value)}
                                            min={0}
                                            className="w-16 bg-transparent border border-gray-300 rounded-lg px-3 py-3 text-sm outline-none text-center"
                                            placeholder="0"
                                        />
                                        <button
                                            type="button"
                                            onClick={onDiscountRemove}
                                            aria-label="Remove discount"
                                            className="text-gray-400 hover:text-red-500 ml-1"
                                        >
                                            <Trash size={16} />
                                        </button>
                                    </div>
                                    <span className="text-red-500 font-medium">
                                        -{formatCurrency(totals.discountAmount, currency ?? "KES")}
                                    </span>
                                </>
                            )}
                        </div>
                    )
                ) : null}


                {/* VAT */}
                {(vatEnabled || restrictedMode || totals.taxTotal > 0) && (
                    <>
                        {/* <Divider /> */}

                        <div className="flex justify-between items-center text-gray-800">
                            <span>{vatLabel}</span>
                            <span>{formatCurrency(totals.taxTotal, currency)}</span>
                        </div>
                    </>
                )}

                <Divider />

                {/* Total Due */}
                <div className="flex justify-between items-center font-bold text-[16px] text-gray-900">
                    <span>Total Due</span>
                    <span>{formatCurrency(totals.totalDue, currency ?? "KES")}</span>
                </div>

                {/* Payment Info (viewer mode only) */}
                {(amountPaid ?? 0) > 0 && (
                    <>
                        <Divider />
                        <div className="flex justify-between items-center font-bold text-[16px] text-gray-900">
                            <span>Amount Paid</span>
                            <span>{formatCurrency(amountPaid ?? 0, currency ?? "KES")}</span>
                        </div>
                        <div className="flex justify-between items-center font-bold text-[16px] text-gray-900">
                            <span>Balance Due</span>
                            <span>{formatCurrency(balanceDue ?? 0, currency ?? "KES")}</span>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

