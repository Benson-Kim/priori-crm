export interface VatRateOption {
    value: string;
    label: string;
    disabled?: boolean;
}

export type LineTaxOption = VatRateOption;

export const PETROLEUM_VAT_8_FIRST_START = "2018-09-01";
export const PETROLEUM_VAT_8_FIRST_END = "2023-06-30";
export const PETROLEUM_VAT_13_START = "2026-04-15";
export const PETROLEUM_VAT_13_END = "2026-04-15";
export const PETROLEUM_VAT_8_SECOND_START = "2026-04-16";
export const PETROLEUM_VAT_8_SECOND_END = "2026-10-14";

// Retained for source compatibility only. Validation no longer treats every
// generic 8% tax point before this date as valid.
export const VAT_8_LAST_VALID_DATE = PETROLEUM_VAT_8_FIRST_END;

interface DateInterval {
    start: string;
    end: string;
}

const PETROLEUM_INTERVALS: Readonly<Record<string, readonly DateInterval[]>> = {
    petroleum_vat_8: [
        {
            start: PETROLEUM_VAT_8_FIRST_START,
            end: PETROLEUM_VAT_8_FIRST_END,
        },
        {
            start: PETROLEUM_VAT_8_SECOND_START,
            end: PETROLEUM_VAT_8_SECOND_END,
        },
    ],
    petroleum_vat_13: [
        {
            start: PETROLEUM_VAT_13_START,
            end: PETROLEUM_VAT_13_END,
        },
    ],
};

const DOCUMENT_VAT_OPTIONS: readonly VatRateOption[] = [
    { value: "16", label: "16%" },
    { value: "0", label: "0%" },
];

const LINE_TAX_OPTIONS: readonly LineTaxOption[] = [
    { value: "vat_16", label: "16%" },
    { value: "petroleum_vat_13", label: "13% (petroleum only)" },
    { value: "petroleum_vat_8", label: "8% (petroleum only)" },
    { value: "vat_0", label: "0%" },
];

function normalizedVATRate(rate?: string): string | undefined {
    if (rate == null || rate.trim() === "") return undefined;
    const number = Number.parseFloat(rate);
    return Number.isFinite(number) ? String(Number(number.toFixed(4))) : rate;
}

function isWithinInterval(taxPointDate: string, interval: DateInterval): boolean {
    return taxPointDate >= interval.start && taxPointDate <= interval.end;
}

export function isLineTaxTreatmentValidForDate(
    treatment: string,
    taxPointDate?: string
): boolean {
    if (treatment === "vat_8") return false;
    const intervals = PETROLEUM_INTERVALS[treatment];
    if (intervals == null) return true;
    if (!taxPointDate) return false;
    return intervals.some((interval) => isWithinInterval(taxPointDate, interval));
}

export function lineTaxValidationError(
    taxType: string,
    taxPointDate?: string
): string | undefined {
    if (taxType === "vat_8") {
        return "Legacy unscoped VAT 8% cannot be selected for new or edited items";
    }
    if (!Object.hasOwn(PETROLEUM_INTERVALS, taxType)) return undefined;
    if (!taxPointDate) return "Select a tax point date for the petroleum VAT rate";
    if (isLineTaxTreatmentValidForDate(taxType, taxPointDate)) return undefined;
    return "This petroleum VAT treatment is not valid for the selected tax point";
}

function lineTaxLabel(treatment: string): string {
    const known = LINE_TAX_OPTIONS.find((option) => option.value === treatment);
    if (known) return known.label;
    if (treatment === "vat_8") return "8% (legacy unscoped; not selectable)";
    return `${treatment.replaceAll("_", " ")} (stored treatment)`;
}

export function getLineTaxOptions(
    taxPointDate?: string,
    selectedTreatment?: string
): readonly LineTaxOption[] {
    const options = LINE_TAX_OPTIONS.filter((option) =>
        isLineTaxTreatmentValidForDate(option.value, taxPointDate)
    ).map((option) => ({ ...option }));
    if (
        selectedTreatment == null ||
        options.some((option) => option.value === selectedTreatment)
    ) {
        return options;
    }
    options.splice(1, 0, {
        value: selectedTreatment,
        label: lineTaxLabel(selectedTreatment),
        disabled: true,
    });
    return options;
}

export function isVatRateValidForDate(
    rate: string | number | null | undefined,
    _taxPointDate?: string
): boolean {
    void _taxPointDate;
    if (rate == null || rate === "") return true;
    const number = typeof rate === "number" ? rate : Number.parseFloat(rate);
    if (!Number.isFinite(number)) return false;
    return number !== 8 && number !== 13;
}

export function vatRateValidationError(
    rate: string | number | null | undefined,
    taxPointDate?: string
): string | undefined {
    if (rate == null || rate === "") return "Select a VAT rate";
    const number = typeof rate === "number" ? rate : Number.parseFloat(rate);
    if (!Number.isFinite(number)) return "Enter a valid VAT rate";
    if (!isVatRateValidForDate(number, taxPointDate)) {
        return "Petroleum VAT rates require a petroleum-scoped line treatment";
    }
    return undefined;
}

export function getVatRateOptions(
    _taxPointDate?: string,
    selectedRate?: string
): readonly VatRateOption[] {
    void _taxPointDate
    const options = DOCUMENT_VAT_OPTIONS.map((option) => ({ ...option }));
    const selected = normalizedVATRate(selectedRate);
    if (selected == null || options.some((option) => option.value === selected)) {
        return options;
    }
    const number = Number.parseFloat(selected);
    const petroleumRate = number === 8 || number === 13;
    options.splice(1, 0, {
        value: selected,
        label: petroleumRate
            ? `${selected}% (petroleum scope unavailable)`
            : `${selected}% (stored rate)`,
        disabled: true,
    });
    return options;
}