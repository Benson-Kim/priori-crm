/**
 * LineItemsTable — shared line items editor for Invoice and Quote forms.
 * Handles item rows, tax rows, add/remove actions.
 */

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { TAX_CATEGORY_OPTIONS } from "@/lib/constants";
import { getLineTaxOptions, lineTaxValidationError } from "@/lib/taxUtils";
import { formatCurrency, } from "@/lib/utils";
import { Plus, Trash } from "lucide-react";
import { Fragment } from "react";
import {
    buildTaxType,
    calcLineTotal,
    calcTaxAmount,
    parseTaxType,
    type LineItemRow,
} from "../utils";

interface LineItemsTableProps {
    lineItems: LineItemRow[];
    errors: Record<string, string>;
    restrictedMode: boolean;
    enableInlineTax?: boolean;
    taxPointDate?: string;
    onAddRow: () => void;
    onRemoveRow: (key: string) => void;
    onUpdateRow: (key: string, field: keyof LineItemRow, value: string) => void;
}

export function LineItemsTable({
    lineItems,
    errors,
    restrictedMode,
    enableInlineTax = false,
    taxPointDate,
    onAddRow,
    onRemoveRow,
    onUpdateRow,
}: Readonly<LineItemsTableProps>) {
    return (
        <div className="flex flex-col gap-3 pb-4">
            <table className="w-full text-base">
                <thead>
                    <tr className="bg-priori-purple text-white px-3 py-4 grid grid-cols-7 rounded-t-lg">
                        <th className="text-left px-3 font-bold leading-8 col-span-2">Item</th>
                        <th className="text-left px-3 font-bold leading-8 col-span-2">Description</th>
                        <th className="text-left px-3 font-bold leading-8">Quantity</th>
                        <th className="text-left px-3 font-bold leading-8">Price</th>
                        <th className="text-left px-3 font-bold leading-8">Total</th>
                    </tr>
                </thead>
                <tbody>
                    {lineItems.map((row) => (
                        <LineItemRows
                            key={row.key}
                            row={row}
                            errors={errors}
                            enableInlineTax={enableInlineTax}
                            restrictedMode={restrictedMode}
                            taxPointDate={taxPointDate}
                            onRemoveRow={onRemoveRow}
                            onUpdateRow={onUpdateRow}
                        />
                    ))}
                </tbody>
            </table>

            {!restrictedMode && (
                <Button variant="outline" onClick={onAddRow} className="w-max">
                    <Plus size={20} /> Add an Item
                </Button>
            )}
        </div>
    );
}

// Private sub-component for a single row pair (item + tax) 

interface LineItemRowsProps {
    row: LineItemRow;
    errors: Record<string, string>;
    restrictedMode: boolean;
    enableInlineTax: boolean;
    taxPointDate?: string;
    onRemoveRow: (key: string) => void;
    onUpdateRow: (key: string, field: keyof LineItemRow, value: string) => void;
}

function LineItemRows({
    row,
    errors,
    restrictedMode,
    enableInlineTax,
    taxPointDate,
    onRemoveRow,
    onUpdateRow,
}: LineItemRowsProps) {
    const lineTotal = calcLineTotal(row.quantity, row.unitPrice);
    const taxAmount = calcTaxAmount(lineTotal, row.taxType);
    const { category: taxCategory, isVat: hasTax } = parseTaxType(row.taxType);
    const hasInlineTax = row.taxType !== "no_tax";

    const taxError = errors[`item_${row.key}_tax`] ?? (
        hasTax ? lineTaxValidationError(row.taxType, taxPointDate) : undefined
    );

    const handleCategoryChange = (val: string) => {
        if (val === "no_tax" || val === "exempt") {
            onUpdateRow(row.key, "taxType", val);
        } else {
            onUpdateRow(row.key, "taxType", buildTaxType("vat", "16"));
        }
    };

    const handleRateChange = (val: string) => {
        onUpdateRow(row.key, "taxType", val);
    };

    return (
        <Fragment>
            {/* Item Row */}
            <tr className="group relative mb-3 grid grid-cols-7 gap-4 pt-2">
                {/* Item Name */}
                <td className="align-top col-span-2">
                    <Input
                        id={row.itemName}
                        type="text"
                        value={row.itemName}
                        onChange={(e) => onUpdateRow(row.key, "itemName", e.target.value)}
                        disabled={restrictedMode}
                        error={errors[`item_${row.key}_name`]}
                        placeholder="Item name"
                        wrapperClassName="bg-white"
                        className="font-bold"
                    />
                </td>

                {/* Description */}
                <td className="align-top col-span-2">
                    <textarea
                        value={row.description}
                        onChange={(e) => onUpdateRow(row.key, "description", e.target.value)}
                        placeholder="Detailed description"
                        disabled={restrictedMode}
                        rows={1}
                        className="flex items-center w-full bg-transparent py-4 px-3 gap-3 rounded-lg border border-gray-300 focus-within:border-priori-purple focus-within:ring-priori-purple/10 resize-none transition-all"
                    />
                </td>

                {/* Quantity */}
                <td className="align-top">
                    <Input
                        id={row.quantity}
                        type="number"
                        min={0}
                        value={row.quantity}
                        onChange={(e) => onUpdateRow(row.key, "quantity", e.target.value)}
                        disabled={restrictedMode}
                        error={errors[`item_${row.key}_qty`]}
                        wrapperClassName="bg-white"
                    />
                </td>

                {/* Unit Price */}
                <td className="align-top">
                    <Input
                        id={row.unitPrice}
                        type="number"
                        min={0}
                        value={row.unitPrice}
                        onChange={(e) => onUpdateRow(row.key, "unitPrice", e.target.value)}
                        disabled={restrictedMode}
                        error={errors[`item_${row.key}_price`]}
                        wrapperClassName="bg-white"
                    />

                </td>

                {/* Line Total */}
                <td className="px-3 py-4 text-left border border-gray-100 bg-[#F8F9FA] text-gray-800 flex items-center font-bold rounded-lg">
                    {formatCurrency(lineTotal, "")}
                </td>

                {/* Delete Row Button */}
                {!restrictedMode && (
                    <DeleteRowButton onClick={() => onRemoveRow(row.key)} />
                )}
            </tr>

            {/* Tax Row */}
            {enableInlineTax && hasInlineTax ? (
                <tr className="group relative mb-3 grid grid-cols-7 gap-4 items-center">
                    <td className="col-span-4 flex justify-end items-center pr-4 font-bold text-gray-800">
                        Tax
                    </td>

                    {/* Tax Category Select */}
                    <td className="col-span-1">
                        <Select
                            id={taxCategory}
                            value={taxCategory}
                            onChange={(e) => handleCategoryChange(e.target.value)}
                            disabled={restrictedMode}
                            options={TAX_CATEGORY_OPTIONS}
                            wrapperClassName="bg-white"
                        />
                    </td>

                    {/* VAT Rate Select */}
                    <td className="col-span-1">
                        {hasTax && (
                            <Select
                                id={`${row.key}-tax-treatment`}
                                value={row.taxType}
                                onChange={(e) => handleRateChange(e.target.value)}
                                disabled={restrictedMode}
                                options={getLineTaxOptions(taxPointDate, row.taxType)}
                                error={taxError}
                                wrapperClassName="bg-white"
                            />
                        )}
                    </td>

                    {/* Tax Amount */}
                    <td className="px-3 py-4 text-left border border-gray-100 bg-[#F8F9FA] text-gray-800 flex items-center font-bold rounded-lg">
                        {formatCurrency(taxAmount, "")}
                    </td>

                    {/* Remove Tax Button */}
                    {!restrictedMode && (
                        <DeleteRowButton
                            onClick={() => onUpdateRow(row.key, "taxType", "no_tax")}
                        />
                    )}
                </tr>
            ) : enableInlineTax && !restrictedMode ? (
                <tr className="mb-3 grid grid-cols-7 gap-4">
                    <td className="col-span-7 flex justify-end">
                        <button
                            type="button"
                            onClick={() => onUpdateRow(row.key, "taxType", "vat_16")}
                            className="text-priori-purple text-sm font-bold hover:underline"
                        >
                            + Add Tax
                        </button>
                    </td>
                </tr>
            ) : null}
        </Fragment>
    );
}

// Delete button 

function DeleteRowButton({ onClick }: { onClick: () => void }) {
    return (
        <div className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-full pl-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
                type="button"
                onClick={onClick}
                aria-label="Remove row"
                className="text-gray-400 hover:text-red-500 transition-colors"
            >
                <Trash size={20} />
            </button>
        </div>
    );
}