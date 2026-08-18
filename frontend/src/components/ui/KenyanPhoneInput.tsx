/**
 * KenyanPhoneInput
 *
 * The as-you-type phone field the customer form introduced, lifted out so
 * every phone field in the app behaves the same way: a fixed `+254` prefix,
 * digits grouped `7XX XXX XXX` as they are typed, and ghost text showing the
 * shape still to come.
 *
 * Named for the country it handles rather than `PhoneInput`, because it is
 * not a general phone field and must not be pointed at one. It strips a
 * leading `254` or `0` and clamps to nine digits, which is right for a Kenyan
 * number and destructive for anything else — `+256772123456` would be read as
 * the digits `256772123`. Where a form lets the user pick from the full
 * country list, gate this on Kenya and fall back to a plain text field.
 *
 * The contract is deliberately narrow: **local digits in, local digits out**.
 * Callers map that to whatever their own module stores — customers keep bare
 * digits (`customerSchema` validates `^[0-9]{9,10}$`), vendors keep the
 * `+254…` form the server already holds — so no module's stored shape changes
 * just because the field was rendered.
 */

import { forwardRef } from "react";

import { Input } from "@/components/ui/Input";
import {
    formatKenyanPhoneInput,
    getKenyanPhoneGhostText,
    toLocalKenyanDigits,
} from "@/lib/utils";

interface KenyanPhoneInputProps {
    id?: string;
    name?: string;
    /** The nine local digits, without the country code or a leading zero. */
    value: string;
    /** Receives the nine local digits, never the formatted display string. */
    onChange: (localDigits: string) => void;
    onBlur?: () => void;
    error?: string;
    disabled?: boolean;
    required?: boolean;
    "aria-describedby"?: string;
}

export const KenyanPhoneInput = forwardRef<HTMLInputElement, KenyanPhoneInputProps>(
    function KenyanPhoneInput({ value, onChange, ...props }, ref) {
        const formattedValue = formatKenyanPhoneInput(value ?? "");

        return (
            <Input
                {...props}
                ref={ref}
                type="tel"
                prefix="+254"
                inputMode="numeric"
                autoComplete="tel-national"
                value={formattedValue}
                ghostText={getKenyanPhoneGhostText(formattedValue)}
                onChange={(event) => {
                    /*
                     * Read the digits back out of the formatted string, drop a
                     * country code or trunk zero the user pasted, and cap at
                     * the nine a Kenyan number has. Everything the field emits
                     * is therefore already in the stored shape.
                     */
                    onChange(toLocalKenyanDigits(event.target.value));
                }}
                /*
                 * The ghost text is the placeholder here; a real one would
                 * print underneath it.
                 */
                placeholder=""
            />
        );
    }
);
